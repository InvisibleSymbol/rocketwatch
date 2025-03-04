import asyncio
import copy
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import cronitor
import pymongo
from discord.ext import commands, tasks
from requests.exceptions import HTTPError

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.rocketpool import rp
from utils.shared_w3 import w3, bacon
from utils.solidity import to_float
from utils.time_debug import timerun

log = logging.getLogger("minipool_task")
log.setLevel(cfg["log_level"])

class MinipoolTask(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = pymongo.MongoClient(cfg["mongodb_uri"]).rocketwatch
        self.minipool_manager = rp.get_contract_by_name("rocketMinipoolManager")
        self.monitor = cronitor.Monitor('gather-minipools', api_key=cfg["cronitor_secret"])

        if not self.run_loop.is_running() and bot.is_ready():
            self.run_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.run_loop.is_running():
            return
        self.run_loop.start()

    @tasks.loop(seconds=60 ** 2)
    async def run_loop(self):
        p_id = time.time()
        self.monitor.ping(state='run', series=p_id)
        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()
        futures = [loop.run_in_executor(executor, self.task)]
        try:
            await asyncio.gather(*futures)
            self.monitor.ping(state='complete', series=p_id)
        except Exception as err:
            await self.bot.report_error(err)
            self.monitor.ping(state='fail', series=p_id)

    @timerun
    def get_untracked_minipools(self):
        minipool_count = rp.call("rocketMinipoolManager.getMinipoolCount")
        minipool_addresses = []
        for i in range(0, minipool_count, 10000):
            log.debug(f"getting minipool addresses for {i}/{minipool_count}")
            i_end = min(i + 10000, minipool_count)
            minipool_addresses += [
                w3.toChecksumAddress(r.results[0]) for r in rp.multicall.aggregate(
                    self.minipool_manager.functions.getMinipoolAt(i) for i in range(i, i_end)).results]
        # remove address that are already in the minipool collection
        tracked_addresses = self.db.minipools.distinct("address")
        return [a for a in minipool_addresses if a not in tracked_addresses]

    @timerun
    def get_public_keys(self, addresses):
        # optimizing this doesn't seem to help much, so keep it simple for readability
        # batch the same way as get_untracked_minipools
        minipool_pubkeys = []
        for i in range(0, len(addresses), 10000):
            log.debug(f"getting minipool pubkeys for {i}/{len(addresses)}")
            i_end = min(i + 10000, len(addresses))
            minipool_pubkeys += [
                f"0x{r.results[0].hex()}" for r in rp.multicall.aggregate(
                    self.minipool_manager.functions.getMinipoolPubkey(a) for a in addresses[i:i_end]).results]
        return minipool_pubkeys

    @timerun
    def get_node_operator(self, addresses):
        base_contract = rp.assemble_contract("rocketMinipool", w3.toChecksumAddress(addresses[0]))
        func = base_contract.functions.getNodeAddress()
        minipool_contracts = []
        for a in addresses:
            tmp = copy.deepcopy(func)
            tmp.address = w3.toChecksumAddress(a)
            minipool_contracts.append(tmp)
        node_addresses = rp.multicall.aggregate(minipool_contracts)
        node_addresses = [w3.toChecksumAddress(r.results[0]) for r in node_addresses.results]
        return node_addresses

    @timerun
    def get_node_fee(self, addresses):
        base_contract = rp.assemble_contract("rocketMinipool", w3.toChecksumAddress(addresses[0]))
        func = base_contract.functions.getNodeFee()
        minipool_contracts = []
        for a in addresses:
            tmp = copy.deepcopy(func)
            tmp.address = w3.toChecksumAddress(a)
            minipool_contracts.append(tmp)
        node_fees = rp.multicall.aggregate(minipool_contracts)
        node_fees = [to_float(r.results[0]) for r in node_fees.results]
        return node_fees

    @timerun
    def get_validator_data(self, pubkeys):
        result = {}
        pubkeys_divisor = max(len(pubkeys) // 10, 1)  # Make sure divisor is at least 1 to avoid division by zero
        for i, pubkey in enumerate(pubkeys):
            if i % pubkeys_divisor == 0:
                log.debug(f"getting validator data for {i}/{len(pubkeys)}")
            try:
                data = bacon.get_validator(validator_id=pubkey, state_id="finalized")
            except HTTPError:
                continue
            data = data["data"]
            validator_id = int(data["index"])
            activation_epoch = int(data["validator"]["activation_epoch"])
            # The activation epoch is set to the possible maximum int if none has been determined yet.
            # I don't check for an exact value because it turns out that nimbus uses uint64 while Teku uses int64.
            # >=2**23 will be good enough for the next 100 years, after which neither this bot nor its creator will be alive.
            if activation_epoch >= 2 ** 23:
                continue
            result[pubkey] = {"validator_id": validator_id, "activation_epoch": activation_epoch}
        return result

    def check_indexes(self):
        log.debug("checking indexes")
        self.db.proposals.create_index("validator")
        # self.db.minipools.create_index("validator", unique=True)
        # remove the old unique validator index if it exists, create a new one without unique called validator_2
        if "validator_1" in self.db.minipools.index_information():
            self.db.minipools.drop_index("validator_1")
        self.db.minipools.create_index("validator", name="validator_2")
        self.db.proposals.create_index("slot", unique=True)
        self.db.minipools.create_index("address")
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
        log.debug("Gathering all Minipool commission rates...")
        node_fees = self.get_node_fee(minipool_addresses)
        log.debug("Gathering all Minipool validator indexes...")
        validator_data = self.get_validator_data(minipool_pubkeys)
        data = [{
            "address"         : a,
            "pubkey"          : p,
            "node_operator"   : n,
            "node_fee"        : f,
            "validator"       : validator_data[p]["validator_id"],
            "activation_epoch": validator_data[p]["activation_epoch"]
        } for a, p, n, f in zip(minipool_addresses, minipool_pubkeys, node_addresses, node_fees) if p in validator_data]
        if data:
            log.debug(f"Inserting {len(data)} Minipools into the database...")
            self.db.minipools.insert_many(data)
        else:
            log.debug("No new Minipools with data found.")
        log.debug("Finished!")

    def cog_unload(self):
        self.run_loop.cancel()


async def setup(bot):
    await bot.add_cog(MinipoolTask(bot))
