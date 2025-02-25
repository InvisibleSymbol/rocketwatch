import logging

import pymongo
import requests
from eth_typing import BlockNumber
from web3.datastructures import MutableAttributeDict as aDict

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import assemble, prepare_args
from utils.readable import cl_explorer_url
from utils.rocketpool import rp
from utils.shared_w3 import bacon, w3
from utils.solidity import date_to_beacon_block
from utils.event import EventPlugin, Event

log = logging.getLogger("beacon_slashings")
log.setLevel(cfg["log_level"])


class Slashings(EventPlugin):
    def __init__(self, bot: RocketWatch):
        super().__init__(bot)
        self.db = pymongo.MongoClient(cfg["mongodb_uri"]).rocketwatch

    def _get_new_events(self) -> list[Event]:
        from_block = self.last_served_block + 1 - self.lookback_distance
        return self.get_slashings(from_block, self._pending_block)

    def _get_past_events(self, from_block: BlockNumber, to_block: BlockNumber) -> list[Event]:
        return self.get_slashings(from_block, to_block)

    def get_slashings(self, from_block: BlockNumber, to_block: BlockNumber) -> list[Event]:
        log.info("Checking for new slashings")
        events = []

        for block_number in range(from_block, to_block):
            log.debug(f"Checking block {block_number}")

            execution_block = w3.eth.get_block(block_number)
            timestamp = execution_block.timestamp
            slot_number = date_to_beacon_block(timestamp)
            if slot_number < 0:
                log.debug("Pre beacon chain block, skipping")
                continue

            try:
                beacon_block = bacon.get_block(slot_number)["data"]["message"]
            except ValueError as e:
                if e.args[0] != "Block does not exist":
                    raise e
                log.error(f"Beacon block {slot_number} not found. Skipping.")
                continue

            slashings = []
            for slash in beacon_block["body"]["attester_slashings"]:
                set_a = set(slash["attestation_2"]["attesting_indices"])
                offending_indices = set_a.intersection(slash["attestation_1"]["attesting_indices"])
                slashings.extend(
                    {
                        "slashing_type": "Attestation",
                        "minipool"     : index,
                        "slasher"      : beacon_block["proposer_index"],
                        "timestamp"    : timestamp
                    } for index in offending_indices)

            slashings.extend(
                {
                    "slashing_type": "Proposal",
                    "minipool"     : slash["signed_header_1"]["message"]["proposer_index"],
                    "slasher"      : beacon_block["proposer_index"],
                    "timestamp"    : timestamp
                } for slash in beacon_block["body"]["proposer_slashings"])

            for slash in slashings:
                lookup = self.db.minipools.find_one({"validator": int(slash["minipool"])})
                if not lookup:
                    log.info(f"Skipping slash of unknown validator {slash['minipool']}")
                    continue
                unique_id = f"{timestamp}" \
                            f":slash-{slash['minipool']}" \
                            f":slasher-{slash['slasher']}" \
                            f":slashing-type-{slash['slashing_type']}"
                slash["minipool"] = cl_explorer_url(slash["minipool"])
                slash["slasher"] = cl_explorer_url(slash["slasher"])
                slash["node_operator"] = lookup["node_operator"]
                slash["event_name"] = "minipool_slash_event"
                args = prepare_args(aDict(slash))
                if embed := assemble(args):
                    events.append(Event(
                        topic="beacon_slashings",
                        embed=embed,
                        event_name=slash["event_name"],
                        unique_id=unique_id,
                        block_number=block_number
                    ))

            # track proposals made by rocket pool validators. use mongodb minipools collection to check
            if (m := self.db.minipools.find_one({"validator": int(beacon_block["proposer_index"])})) and "execution_payload" in beacon_block[
                "body"]:
                # fetch the values from beaconcha.in. we use that instead of the beacon node because the beacon node
                # has no idea about mev bribes
                req = requests.get(f"{cfg['beaconchain_explorer']['api']}/api/v1/execution/block/{block_number}",
                                   headers={"apikey": cfg["beaconchain_explorer"]["api_key"]})
                log.info(f"Rocket Pool validator {beacon_block['proposer_index']} made a proposal")
                if req.status_code == 200:
                    res = req.json()
                    log.debug(f"{res=}")
                    req = res["data"][0]
                    log.debug(f"Proposal data: {req}")
                    if (a := solidity.to_float(req["producerReward"])) > 1:
                        log.info(f"Found a proposal with a mev bribe of {a} ETH")
                        fee_recipient = req["relay"]["producerFeeRecipient"] if req["relay"] else req["feeRecipient"]
                        args = {
                            "event_name"   :
                                "mev_proposal_smoothie_event"
                                if fee_recipient.lower() == rp.get_address_by_name("rocketSmoothingPool").lower()
                                else "mev_proposal_event",
                            "node_operator": m["node_operator"],
                            "minipool"     : m["address"],
                            "slot"  : beacon_block["slot"],
                            "reward_amount": a,
                            "timestamp"    : timestamp,
                        }
                        if "smoothie" in args["event_name"]:
                            args["smoothie_amount"] = rp.call("multicall3.getEthBalance", w3.toChecksumAddress(fee_recipient), block=block_number)
                        args = prepare_args(aDict(args))
                        if embed := assemble(args):
                            events.append(Event(
                                topic="mev_proposals",
                                embed=embed,
                                event_name=args["event_name"],
                                unique_id=f"{timestamp}:mev_proposal-{block_number}",
                                block_number=block_number
                            ))

            # alerts for non-finality issue
            current_epoch = slot_number // 32
            # calculate finality delay
            finality_checkpoint = bacon.get_finality_checkpoint(state_id=slot_number)
            finality_delay = current_epoch - int(finality_checkpoint["data"]["finalized"]["epoch"])
            # if delay is over 2 epochs, alert
            if finality_delay > 2:
                log.warning(f"Finality delay is {finality_delay} epochs")
                args = {
                    "event_name"   : "finality_delay_event",
                    "finality_delay": finality_delay,
                    "timestamp"    : timestamp,
                    "epoch"        : current_epoch
                }
                args = prepare_args(aDict(args))
                if embed := assemble(args):
                    events.append(Event(
                        topic="finality_delay",
                        embed=embed,
                        event_name=args["event_name"],
                        unique_id=f"{current_epoch}:finality_delay",
                        block_number=block_number
                    ))
            # latest finality delay from db
            latest_finality_delay = self.db.finality_checkpoints.find_one({"epoch": current_epoch - 1})
            if latest_finality_delay:
                latest_finality_delay = latest_finality_delay["finality_delay"]
            else:
                latest_finality_delay = 2

            # if finality delay recovers, notify
            if finality_delay <= 2 < latest_finality_delay:
                log.info(f"Finality delay recovered from {latest_finality_delay} to {finality_delay}")
                args = {
                    "event_name"   : "finality_delay_recover_event",
                    "finality_delay": finality_delay,
                    "timestamp"    : timestamp,
                    "epoch"        : current_epoch
                }
                args = prepare_args(aDict(args))
                if embed := assemble(args):
                    events.append(Event(
                        topic="finality_delay_recover",
                        embed=embed,
                        event_name=args["event_name"],
                        unique_id=f"{current_epoch}:finality_delay_recover",
                        block_number=block_number
                    ))

            self.db.finality_checkpoints.update_one(
                {"epoch": current_epoch},
                {"$set": {"finality_delay": finality_delay}},
                upsert=True
            )

        log.debug("Finished checking for new slashings")
        return events


async def setup(bot):
    await bot.add_cog(Slashings(bot))
