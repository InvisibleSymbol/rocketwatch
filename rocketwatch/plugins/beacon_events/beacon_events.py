import logging
from typing import Optional, cast

import pymongo
import requests
import eth_utils
from eth_typing import BlockNumber
from web3.datastructures import MutableAttributeDict as aDict

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import assemble, prepare_args
from utils.readable import cl_explorer_url
from utils.rocketpool import rp
from utils.shared_w3 import bacon, w3
from utils.solidity import date_to_beacon_block, beacon_block_to_date
from utils.event import EventPlugin, Event
from utils.get_nearest_block import get_block_by_timestamp
from utils.retry import retry

log = logging.getLogger("beacon_events")
log.setLevel(cfg["log_level"])


class BeaconEvents(EventPlugin):
    def __init__(self, bot: RocketWatch):
        super().__init__(bot)
        self.db = pymongo.MongoClient(cfg["mongodb.uri"]).rocketwatch
        self.finality_delay_threshold = 3

    def _get_new_events(self) -> list[Event]:
        from_block = self.last_served_block + 1 - self.lookback_distance
        return self.get_past_events(from_block, self._pending_block)

    def get_past_events(self, from_block: BlockNumber, to_block: BlockNumber) -> list[Event]:
        from_slot = max(0, date_to_beacon_block(w3.eth.get_block(from_block - 1).timestamp) + 1)
        to_slot = date_to_beacon_block(w3.eth.get_block(to_block).timestamp)
        log.info(f"Checking for new beacon chain events in slot range [{from_slot}, {to_slot}]")

        events: list[Event] = []
        for slot_number in range(from_slot, to_slot-1):
            events.extend(self._get_events_for_slot(slot_number, check_finality=False))

        # quite expensive and only really makes sense to check toward the head of the chain
        events.extend(self._get_events_for_slot(to_slot, check_finality=True))

        log.debug("Finished checking beacon chain events")
        return events

    def _get_events_for_slot(self, slot_number: int, *, check_finality: bool) -> list[Event]:
        try:
            log.debug(f"Checking slot {slot_number}")
            beacon_block = bacon.get_block(slot_number)["data"]["message"]
        except ValueError as err:
            if err.args[0] == "Block does not exist":
                log.error(f"Beacon block {slot_number} not found, skipping.")
                return []
            raise err

        events = self._get_slashings(beacon_block)
        if proposal_event := self._get_proposal(beacon_block):
            events.append(proposal_event)

        if check_finality and (finality_delay_event := self._check_finality(beacon_block)):
            events.append(finality_delay_event)

        return events

    def _get_slashings(self, beacon_block: dict) -> list[Event]:
        slot = int(beacon_block["slot"])
        timestamp = beacon_block_to_date(slot)
        slashings = []

        for slash in beacon_block["body"]["attester_slashings"]:
            att_1 = set(slash["attestation_1"]["attesting_indices"])
            att_2 = set(slash["attestation_2"]["attesting_indices"])
            slashings.extend({
                "slashing_type": "Attestation",
                "minipool"     : index,
                "slasher"      : beacon_block["proposer_index"],
                "timestamp"    : timestamp
            } for index in att_1.intersection(att_2))

        slashings.extend({
            "slashing_type": "Proposal",
            "minipool"     : slash["signed_header_1"]["message"]["proposer_index"],
            "slasher"      : beacon_block["proposer_index"],
            "timestamp"    : timestamp
        } for slash in beacon_block["body"]["proposer_slashings"])

        events = []
        for slash in slashings:
            minipool = self.db.minipools.find_one({"validator": int(slash["minipool"])})
            if not minipool:
                log.info(f"Skipping slashing of unknown validator {slash['minipool']}")
                continue

            unique_id = (
                f"slash-{slash['minipool']}"
                f":slasher-{slash['slasher']}"
                f":slashing-type-{slash['slashing_type']}"
                f":{timestamp}"
            )
            slash["minipool"] = cl_explorer_url(slash["minipool"])
            slash["slasher"] = cl_explorer_url(slash["slasher"])
            slash["node_operator"] = minipool["node_operator"]
            slash["event_name"] = "minipool_slash_event"

            args = prepare_args(aDict(slash))
            if embed := assemble(args):
                events.append(Event(
                    topic="beacon_events",
                    embed=embed,
                    event_name=slash["event_name"],
                    unique_id=unique_id,
                    block_number=get_block_by_timestamp(timestamp)[0],
                ))

        return events

    @retry(tries=3, delay=10)
    def _get_proposal(self, beacon_block: dict) -> Optional[Event]:
        if not (payload := beacon_block["body"].get("execution_payload")):
            # no proposed block
            return None

        validator_index = int(beacon_block["proposer_index"])
        if not (minipool := self.db.minipools.find_one({"validator": validator_index})):
            # not proposed by a minipool
            return None

        log.info(f"Validator {validator_index} proposed a block")

        timestamp = int(payload["timestamp"])
        block_number = cast(BlockNumber, int(payload["block_number"]))

        if not (api_key := cfg["consensus_layer.beaconcha_secret"]):
            log.warning(f"Missing beaconcha.in API key")
            return None

        # fetch from beaconcha.in because beacon node is unaware of MEV bribes
        endpoint = f"https://beaconcha.in/api/v1/execution/block/{block_number}"
        response = requests.get(endpoint, headers={"apikey": api_key})

        if response.status_code != 200:
            log.warning(f"Error code {response.status_code} from {endpoint}")
            return None

        response_body = response.json()
        log.debug(f"{response_body = }")

        proposal_data = response.json()["data"][0]
        log.debug(f"{proposal_data = }")

        block_reward_eth = solidity.to_float(proposal_data["producerReward"])
        log.info(f"Found a proposal with an MEV bribe of {block_reward_eth} ETH")

        if block_reward_eth <= 1:
            # disregard if proposal reward is below 1 ETH
            return None

        if proposal_data["relay"]:
            fee_recipient = proposal_data["relay"]["producerFeeRecipient"]
        else:
            fee_recipient = proposal_data["feeRecipient"]

        args = {
            "node_operator": minipool["node_operator"],
            "minipool": minipool["address"],
            "slot": int(beacon_block["slot"]),
            "reward_amount": block_reward_eth,
            "timestamp": timestamp,
        }

        if eth_utils.is_same_address(fee_recipient, rp.get_address_by_name("rocketSmoothingPool")):
            args["event_name"] = "mev_proposal_smoothie_event"
            args["smoothie_amount"] = w3.eth.get_balance(
                w3.to_checksum_address(fee_recipient), block_identifier=block_number
            )
        else:
            args["event_name"] = "mev_proposal_event"

        args = prepare_args(aDict(args))
        if not (embed := assemble(args)):
            return None

        return Event(
            topic="mev_proposals",
            embed=embed,
            event_name=args["event_name"],
            unique_id=f"mev_proposal:{block_number}:{timestamp}",
            block_number=block_number
        )

    def _check_finality(self, beacon_block: dict) -> Optional[Event]:
        slot_number = int(beacon_block["slot"])
        epoch_number = slot_number // 32
        timestamp = beacon_block_to_date(slot_number)

        try:
            # calculate finality delay
            finality_checkpoint = bacon.get_finality_checkpoint(state_id=str(slot_number))
            last_finalized_epoch = int(finality_checkpoint["data"]["finalized"]["epoch"])
            finality_delay = epoch_number - last_finalized_epoch
        except requests.exceptions.HTTPError:
            log.exception("Failed to get finality checkpoints")
            return None

        # latest finality delay from db
        delay_entry = self.db.finality_checkpoints.find_one({"epoch": epoch_number - 1})
        prev_finality_delay = delay_entry["finality_delay"] if delay_entry else 0

        self.db.finality_checkpoints.update_one(
            {"epoch": epoch_number},
            {"$set": {"finality_delay": finality_delay}},
            upsert=True
        )

        # if finality delay recovers, notify
        if finality_delay < self.finality_delay_threshold <= prev_finality_delay:
            log.info(f"Finality delay recovered from {prev_finality_delay} to {finality_delay}")
            event_name = "finality_delay_recover_event"
            args = {
                "event_name": event_name,
                "finality_delay": finality_delay,
                "timestamp": timestamp,
                "epoch": epoch_number
            }
            args = prepare_args(aDict(args))
            if not (embed := assemble(args)):
                return None

            event = Event(
                topic="finality",
                embed=embed,
                event_name=event_name,
                unique_id=f"finality_delay_recover:{epoch_number}",
                block_number=get_block_by_timestamp(timestamp)[0]
            )
            return event

        if finality_delay >= max(prev_finality_delay + 1, self.finality_delay_threshold):
            log.warning(f"Finality increased to {finality_delay} epochs")
            event_name = "finality_delay_event"
            args = {
                "event_name"    : event_name,
                "finality_delay": finality_delay,
                "timestamp"     : timestamp,
                "epoch"         : epoch_number
            }
            args = prepare_args(aDict(args))
            if not (embed := assemble(args)):
                return None

            return Event(
                topic="finality",
                embed=embed,
                event_name=event_name,
                unique_id=f"{epoch_number}:finality_delay",
                block_number=get_block_by_timestamp(timestamp)[0]
            )

        return None


async def setup(bot):
    await bot.add_cog(BeaconEvents(bot))
