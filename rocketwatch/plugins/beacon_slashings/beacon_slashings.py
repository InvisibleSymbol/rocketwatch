import logging

import pymongo
import requests
from discord.ext import commands
from web3.datastructures import MutableAttributeDict as aDict

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args
from utils.get_nearest_block import get_block_by_timestamp
from utils.readable import cl_explorer_url
from utils.rocketpool import rp
from utils.shared_w3 import bacon
from utils.solidity import beacon_block_to_date

log = logging.getLogger("beacon_slashings")
log.setLevel(cfg["log_level"])


class QueuedSlashings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo = pymongo.MongoClient(cfg["mongodb_uri"])
        self.db = self.mongo.rocketwatch
        self.state = "INIT"

    def run_loop(self):
        if self.state == "RUNNING":
            log.error("Slashings plugin was interrupted while running. Re-initializing...")
            self.__init__(self.bot)
        return self.check_for_new_slashings()

    def check_for_new_slashings(self):
        log.info("Checking for new Slashings")

        payload = []

        self.state = "RUNNING"
        latest_db_block = self.db.last_checked_block.find_one({"_id": "slashings"})
        node_finalized = int(bacon.get_block("head")["data"]["message"]["slot"]) - 8    
        if not latest_db_block:
            log.info("Doing full check")
            blocks = list(range(node_finalized - cfg["core.look_back_distance"], node_finalized))
        elif latest_db_block["block"] <= node_finalized:
            blocks = list(range(latest_db_block["block"], node_finalized))
        else:
            log.warning(
                "Node is being stupid and returned a block that is smaller than a previously seen finalized block: "
                f"{node_finalized=} < {latest_db_block['block']=}. Skipping this check.")
            return
        for block_number in blocks:
            log.debug(f"Checking Beacon block {block_number}")
            timestamp = beacon_block_to_date(block_number)
            try:
                block = bacon.get_block(block_number)["data"]["message"]
            except ValueError as e:
                if e.args[0] == "Block does not exist":
                    log.error(f"Beacon block {block_number} not found. Skipping.")
                    continue
                raise e
            slashings = []
            for slash in block["body"]["attester_slashings"]:
                set_a = set(slash["attestation_2"]["attesting_indices"])
                offending_indieces = set_a.intersection(slash["attestation_1"]["attesting_indices"])
                slashings.extend(
                    {
                        "slashing_type": "Attestation",
                        "minipool"     : index,
                        "slasher"      : block["proposer_index"],
                        "timestamp"    : timestamp
                    } for index in offending_indieces)

            slashings.extend(
                {
                    "slashing_type": "Proposal",
                    "minipool"     : slash["signed_header_1"]["message"]["proposer_index"],
                    "slasher"      : block["proposer_index"],
                    "timestamp"    : timestamp
                } for slash in block["body"]["proposer_slashings"])

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
                    closest_block = get_block_by_timestamp(timestamp)[0]
                    payload.append(Response(
                        topic="beacon_slashings",
                        embed=embed,
                        event_name=slash["event_name"],
                        unique_id=unique_id,
                        block_number=closest_block
                    ))

            # new-feature: track proposals made by rocket pool validators. use mongodb minipools collection to check
            if (m := self.db.minipools.find_one({"validator": int(block["proposer_index"])})) and "execution_payload" in block[
                "body"]:
                # fetch the values from beaconcha.in. we use that instead of the beacon node because the beacon node
                # has no idea about mev bribes
                exec_block = int(block['body']['execution_payload']['block_number'])
                req = requests.get(f"{cfg['beaconchain_explorer']['api']}/api/v1/execution/block/{exec_block}",
                                   headers={"apikey": cfg["beaconchain_explorer"]["api_key"]})
                log.info(f"Rocket Pool validator {block['proposer_index']} made a proposal")
                if req.status_code == 200:
                    req = req.json()["data"][0]
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
                            "slot"  : block["slot"],
                            "reward_amount": a,
                            "timestamp"    : timestamp
                        }
                        args = prepare_args(aDict(args))
                        if embed := assemble(args):
                            payload.append(Response(
                                topic="mev_proposals",
                                embed=embed,
                                event_name=args["event_name"],
                                unique_id=f"{timestamp}:mev_proposal-{block['body']['execution_payload']['block_number']}",
                                block_number=exec_block
                            ))
                    print(req)

        log.debug("Finished Checking for new Slashes Commands")
        self.state = "OK"

        self.db.last_checked_block.update_one({"_id": "slashings"},
                                              {"$set": {"block": node_finalized}},
                                              upsert=True)

        return payload


async def setup(bot):
    await bot.add_cog(QueuedSlashings(bot))
