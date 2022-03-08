import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pymongo
import requests
from discord.ext import commands, tasks

from utils.cfg import cfg
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.solidity import to_float

log = logging.getLogger("minipool_task")
log.setLevel(cfg["log_level"])


class MinipoolTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo = pymongo.MongoClient(cfg["mongodb_uri"])
        self.db = self.mongo.rocketwatch
        self.minipool_manager = rp.get_contract_by_name("rocketMinipoolManager")

        if not self.run_loop.is_running() and bot.is_ready():
            self.run_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.run_loop.is_running():
            return
        self.run_loop.start()

    @tasks.loop(seconds=60 ** 2)
    async def run_loop(self):
        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()
        futures = [loop.run_in_executor(executor, self.task)]
        try:
            await asyncio.gather(*futures)
        except Exception as err:
            await report_error(err)

    def get_untracked_minipools(self):
        minipool_count = rp.call("rocketMinipoolManager.getMinipoolCount")
        minipool_addresses = rp.multicall.aggregate(
            self.minipool_manager.functions.getMinipoolAt(i) for i in range(minipool_count))
        minipool_addresses = [w3.toChecksumAddress(r.results[0]) for r in minipool_addresses.results]
        # remove address that are already in the minipool collection
        tracked_addresses = self.db.minipools.distinct("address")
        return [a for a in minipool_addresses if a not in tracked_addresses]

    def get_public_keys(self, addresses):
        minipool_pubkeys = rp.multicall.aggregate(self.minipool_manager.functions.getMinipoolPubkey(a) for a in addresses)
        minipool_pubkeys = [f"0x{minipool_pubkey.results[0].hex()}" for minipool_pubkey in minipool_pubkeys.results]
        return minipool_pubkeys

    def get_node_operator(self, addresses):
        minipool_contracts = [rp.assemble_contract("rocketMinipool", w3.toChecksumAddress(a)) for a in addresses]
        node_addresses = rp.multicall.aggregate(m.functions.getNodeAddress() for m in minipool_contracts)
        node_addresses = [w3.toChecksumAddress(r.results[0]) for r in node_addresses.results]
        return node_addresses

    def get_node_fee(self, addresses):
        minipool_contracts = [rp.assemble_contract("rocketMinipool", w3.toChecksumAddress(a)) for a in addresses]
        node_fees = rp.multicall.aggregate(m.functions.getNodeFee() for m in minipool_contracts)
        node_fees = [to_float(r.results[0]) for r in node_fees.results]
        return node_fees

    def get_validator_indexes(self, pubkeys):
        result = {}
        batch_size = 80
        offset = 0
        while True:
            batch = pubkeys[offset:offset + batch_size]
            if not batch:
                break
            log.debug(f"requesting pubkeys {offset} to {min(offset + batch_size, len(pubkeys))}")
            res = requests.get("https://beaconcha.in/api/v1/validator/" + ",".join(batch))
            res = res.json()
            if "data" not in res:
                log.error(f"error getting validator indexes: {res}")
                time.sleep(5)
                continue
            data = res["data"]
            # handle when we only get a single validator back
            if not isinstance(data, list):
                data = [data]
            for validator_data in data:
                validator_id = int(validator_data["validatorindex"])
                pubkey = validator_data["pubkey"]
                result[pubkey] = validator_id
            offset += batch_size
            time.sleep(2)
        return result

    def check_indexes(self):
        log.debug("checking indexes")
        self.db.proposals.create_index("validator")
        self.db.proposals.create_index("slot")
        log.debug("indexes checked")

    def task(self):
        self.check_indexes()
        log.debug("Gathering all untracked Minipools...")
        minipool_addresses = self.get_untracked_minipools()
        if not minipool_addresses:
            log.debug("No untracked Minipools found.")
            return
        log.debug(f"Found {len(minipool_addresses)} untracked Minipools.")
        log.debug("Gathering all Minipool public keys...")
        minipool_pubkeys = self.get_public_keys(minipool_addresses)
        log.debug("Gathering all Minipool node operators...")
        node_addresses = self.get_node_operator(minipool_addresses)
        log.debug("Gather commission rates...")
        node_fees = self.get_node_fee(minipool_addresses)
        log.debug("Gathering all Minipool validator indexes...")
        validator_indexes = self.get_validator_indexes(minipool_pubkeys)
        if data := [
            {
                "address": a,
                "pubkey": p,
                "node_operator": n,
                "node_fee": f,
                "validator": validator_indexes[p],
            }
            for a, p, n, f in zip(
                minipool_addresses, minipool_pubkeys, node_addresses, node_fees
            )
            if p in validator_indexes
        ]:
            log.debug(f"Inserting {len(data)} Minipools into the database...")
            self.db.minipools.insert_many(data)
        else:
            log.debug("No new Minipools with data found.")
        log.debug("Finished!")

    def cog_unload(self):
        self.run_loop.cancel()


def setup(bot):
    bot.add_cog(MinipoolTask(bot))
