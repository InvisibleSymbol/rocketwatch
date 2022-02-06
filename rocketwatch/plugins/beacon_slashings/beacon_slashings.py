import logging

import pymongo
from discord.ext import commands
from requests import HTTPError
from web3.datastructures import MutableAttributeDict as aDict

from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args
from utils.get_nearest_block import get_block_by_timestamp
from utils.readable import beaconchain_url
from utils.shared_w3 import bacon
from utils.solidity import beacon_block_to_date

log = logging.getLogger("beacon_slashings")
log.setLevel(cfg["log_level"])

DEPOSIT_EVENT = 2
WITHDRAWABLE_EVENT = 3


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
        latest_block = self.db.last_checked_block.find_one({"_id": "slashings"})
        head_block = int(bacon.get_block("finalized")["data"]["message"]["slot"])
        if not latest_block:
            log.info("Doing full check")
            blocks = list(range(head_block - cfg["core.look_back_distance"], head_block))
        else:
            blocks = list(range(latest_block["block"], head_block))
        for block_number in blocks:
            log.debug(f"Checking Beacon block {block_number}")
            timestamp = beacon_block_to_date(block_number)
            try:
                block = bacon.get_block(block_number)["data"]["message"]
            except HTTPError as e:
                if e.response.status_code != 404:
                    raise e
                log.error(f"Beacon block {block_number} not found. Skipping.")
                continue
            slashings = []
            for slash in block["body"]["attester_slashings"]:
                set_a = set(slash["attestation_2"]["attesting_indices"])
                offending_indieces = set_a.intersection(slash["attestation_1"]["attesting_indices"])
                for index in offending_indieces:
                    slashings.append({
                        "slashing_type": "Attestation",
                        "minipool"     : index,
                        "slasher"      : block["proposer_index"],
                        "timestamp"    : timestamp
                    })
            for slash in block["body"]["proposer_slashings"]:
                slashings.append({
                    "slashing_type": "Proposal",
                    "minipool"     : slash["signed_header_1"]["message"]["proposer_index"],
                    "slasher"      : block["proposer_index"],
                    "timestamp"    : timestamp
                })
            for slash in slashings:
                lookup = self.db.minipools.find_one({"validator": int(slash["minipool"])})
                if not lookup:
                    log.info(f"Skipping slash of unknown validator {slash['minipool']}")
                    continue
                unique_id = f"{timestamp}:slash-{slash['minipool']}:slasher-{slash['slasher']}:slashing-type-{slash['slashing_type']}"
                slash["minipool"] = beaconchain_url(slash["minipool"])
                slash["slasher"] = beaconchain_url(slash["slasher"])
                slash["node_operator"] = lookup["node_operator"]
                slash["event_name"] = "minipool_slash_event"
                args = prepare_args(aDict(slash))
                embed = assemble(args)
                if embed:
                    closest_block = get_block_by_timestamp(timestamp)[0]
                    payload.append(Response(
                        topic="bootstrap",
                        embed=embed,
                        event_name=slash["event_name"],
                        unique_id=unique_id,
                        block_number=closest_block
                    ))

        log.debug("Finished Checking for new Slashes Commands")
        self.state = "OK"

        self.db.last_checked_block.replace_one({"_id": "slashings"}, {"_id": "slashings", "block": head_block}, upsert=True)

        return payload


def setup(bot):
    bot.add_cog(QueuedSlashings(bot))
