import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import pymongo
from discord.ext import commands, tasks
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient
from multicall import Call

from rocketwatch import RocketWatch
from utils import solidity
from utils.embeds import Embed, el_explorer_url
from utils.readable import s_hex
from utils.shared_w3 import w3
from utils.visibility import is_hidden
from utils.cfg import cfg
from utils.rocketpool import rp
from utils.time_debug import timerun

log = logging.getLogger("minipools_upkeep_task")
log.setLevel(cfg["log_level"])


class MinipoolsUpkeepTask(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.sync_db = pymongo.MongoClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.event_loop = None

        if not self.run_loop.is_running() and bot.is_ready():
            self.run_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.run_loop.is_running():
            return
        self.run_loop.start()

    @timerun
    def get_minipools_from_db(self):
        # get all minipools from db
        m = self.sync_db.minipools.find({}).distinct("address")
        return m

    @timerun
    def get_minipool_stats(self, minipools):
        m_d = rp.get_contract_by_name("rocketMinipoolDelegate")
        m = rp.assemble_contract("rocketMinipool", address=minipools[0])
        mc = rp.get_contract_by_name("multicall3")
        lambs = [
            lambda x: (x, rp.seth_sig(m_d.abi, "getNodeFee"), [((x, "NodeFee"), solidity.to_float)]),
            lambda x: (x, rp.seth_sig(m.abi, "getEffectiveDelegate"), [((x, "Delegate"), None)]),
            lambda x: (x, rp.seth_sig(m.abi, "getPreviousDelegate"), [((x, "PreviousDelegate"), None)]),
            lambda x: (x, rp.seth_sig(m.abi, "getUseLatestDelegate"), [((x, "UseLatestDelegate"), None)]),
            lambda x: (x, rp.seth_sig(m.abi, "getNodeDepositBalance"), [((x, "NodeOperatorShare"), lambda i: solidity.to_float(i) / 32)]),
            # get balances of minipool as well
            lambda x: (mc.address, [rp.seth_sig(mc.abi, "getEthBalance"), x], [((x, "EthBalance"), solidity.to_float)])
        ]
        minipool_stats = {}
        batch_size = 10_000 // len(lambs)
        for i in range(0, len(minipools), batch_size):
            i_end = min(i + batch_size, len(minipools))
            log.debug(f"getting minipool stats for {i}-{i_end}")
            addresses = minipools[i:i_end]
            calls = [
                Call(*lamb(a))
                for a in addresses
                for lamb in lambs
            ]
            res = rp.multicall2_do_call(calls)
            # add data to mini pool stats dict (address => {func_name: value})
            # strip get from function name
            for (address, variable_name), value in res.items():
                if address not in minipool_stats:
                    minipool_stats[address] = {}
                minipool_stats[address][variable_name] = value
        return minipool_stats

    # every 6.4 minutes
    @tasks.loop(seconds=solidity.BEACON_EPOCH_LENGTH)
    async def run_loop(self):
        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()
        futures = [loop.run_in_executor(executor, self.upkeep_minipools)]
        try:
            await asyncio.gather(*futures)
        except Exception as err:
            await self.bot.report_error(err)

    def upkeep_minipools(self):
        logging.info("Updating minipool states")
        # the bellow fixes multicall from breaking
        if not self.event_loop:
            self.event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.event_loop)
        a = self.get_minipools_from_db()
        b = self.get_minipool_stats(a)
        # update data in db using unordered bulk write
        # note: this data is kept in the "meta" field of each minipool
        bulk = [
            pymongo.UpdateOne(
                {"address": address},
                {"$set": {"meta": stats}},
                upsert=True
            ) for address, stats in b.items()
        ]

        self.sync_db.minipools.bulk_write(bulk, ordered=False)
        logging.info("Updated minipool states")

    @hybrid_command()
    async def delegate_stats(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        # get stats about delegates
        # we want to show the distribution of minipools that are using each delegate
        distribution_stats = await self.db.minipools_new.aggregate([
            {"$match": {"effective_delegate": {"$exists": True}}},
            {"$group": {"_id": "$effective_delegate", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]).to_list(None)
        # and the percentage of minipools that are using the useLatestDelegate flag
        use_latest_delegate_stats = await self.db.minipools_new .aggregate([
            {"$match": {"use_latest_delegate": {"$exists": True}}},
            {"$group": {"_id": "$use_latest_delegate", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]).to_list(None)
        e = Embed()
        e.title = "Delegate Stats"
        desc = "**Effective Delegate Distribution of Minipools:**\n"
        c_sum = sum(d['count'] for d in distribution_stats)
        s = "\u00A0" * 4
        # latest delegate acording to rp
        rp.uncached_get_address_by_name("rocketMinipoolDelegate")
        for d in distribution_stats:
            # I HATE THE CHECKSUMMED ADDRESS REQUIREMENTS I HATE THEM SO MUCH
            a = w3.toChecksumAddress(d['_id'])
            name = s_hex(a)
            if a == rp.get_address_by_name("rocketMinipoolDelegate"):
                name += " (Latest)"
            desc += f"{s}{el_explorer_url(a, name)}: {d['count']} ({d['count'] / c_sum * 100:.2f}%)\n"
        desc += "\n"
        desc += "**Minipools configured to always use latest delegate:**\n"
        c_sum = sum(d['count'] for d in use_latest_delegate_stats)
        for d in use_latest_delegate_stats:
            # true = yes, false = no
            d['_id'] = "Yes" if d['_id'] else "No"
            desc += f"{s}**{d['_id']}**: {d['count']} ({d['count'] / c_sum * 100:.2f}%)\n"
        e.description = desc
        await ctx.send(embed=e)


async def setup(self):
    await self.add_cog(MinipoolsUpkeepTask(self))
