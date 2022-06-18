import logging

import aiohttp
from discord.ext import commands
from discord.ext.commands import hybrid_command, Context
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReplaceOne

from utils.cfg import cfg
from utils.embeds import Embed
from utils.embeds import el_explorer_url
from utils.readable import cl_explorer_url
from utils.shared_w3 import bacon
from utils.solidity import BEACON_START_DATE, BEACON_EPOCH_LENGTH
from utils.time_debug import timerun, timerun_async
from utils.visibility import is_hidden

log = logging.getLogger("proposals")
log.setLevel(cfg["log_level"])


class LotteryBase:
    def __init__(self):
        # connect to local mongodb
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.did_check = False

    async def _check_indexes(self):
        if self.did_check:
            return
        log.debug("Checking indexes")
        for period in ["latest", "next"]:
            col = self.db[f"sync_committee_{period}"]
            await col.create_index("validator", unique=True)
            await col.create_index("index", unique=True)
        self.did_check = True
        log.debug("Indexes checked")

    @timerun_async
    async def load_sync_committee(self, period):
        assert period in ["latest", "next"]
        await self._check_indexes()
        h = bacon.get_block("head")
        sync_period = int(h['data']['message']['slot']) // 32 // 256
        if period == "next":
            sync_period += 1
        res = bacon._make_get_request(f"/eth/v1/beacon/states/head/sync_committees?epoch={sync_period * 256}")
        data = res["data"]
        self.db.sync_committee_stats.replace_one({"period": period},
                                                 {"period"     : period,
                                                  "start_epoch": sync_period * 256,
                                                  "end_epoch"  : (sync_period + 1) * 256,
                                                  "sync_period": sync_period * 256,
                                                  }, upsert=True)
        validators = data["validators"]
        col = self.db[f"sync_committee_{period}"]
        payload = [
            ReplaceOne(
                {"index": i}, {"index": i, "validator": int(validator)}, upsert=True
            )
            for i, validator in enumerate(validators)
        ]

        await col.bulk_write(payload)
        return

    async def get_validators_for_sync_committee_period(self, period):
        data = await self.db[f"sync_committee_{period}"].aggregate([
            {
                '$lookup': {
                    'from'        : 'minipools',
                    'localField'  : 'validator',
                    'foreignField': 'validator',
                    'as'          : 'entry'
                }
            }, {
                '$match': {
                    'entry': {
                        '$ne': []
                    }
                }
            }, {
                '$replaceRoot': {
                    'newRoot': {
                        '$first': '$entry'
                    }
                }
            }, {
                '$project': {
                    '_id'          : 0,
                    'validator'    : 1,
                    'pubkey'       : 1,
                    'node_operator': 1
                }
            }, {
                '$match': {
                    'node_operator': {
                        '$ne': None
                    }
                }
            }]).to_list(length=None)

        return data

    async def generate_sync_committee_description(self, period):
        await self.load_sync_committee(period)
        validators = await self.get_validators_for_sync_committee_period(period)
        # get stats about the current period
        stats = await self.db.sync_committee_stats.find_one({"period": period})
        perc = len(validators) / 512
        description = f"_Rocket Pool Participation:_ {len(validators)}/512 ({perc:.2%})\n"
        start_timestamp = BEACON_START_DATE + (stats['start_epoch'] * BEACON_EPOCH_LENGTH)
        description += f"_Start:_ Epoch {stats['start_epoch']} <t:{start_timestamp}> (<t:{start_timestamp}:R>)\n"
        end_timestamp = BEACON_START_DATE + (stats['end_epoch'] * BEACON_EPOCH_LENGTH)
        description += f"_End:_ Epoch {stats['end_epoch']} <t:{end_timestamp}> (<t:{end_timestamp}:R>)\n"
        # validators (called minipools here)
        description += f"_Minipools:_ {', '.join(cl_explorer_url(v['validator']) for v in validators)}\n"
        # node operators
        # gather count per
        node_operators = {}
        for v in validators:
            if v['node_operator'] not in node_operators:
                node_operators[v['node_operator']] = 0
            node_operators[v['node_operator']] += 1
        # sort by count
        node_operators = sorted(node_operators.items(), key=lambda x: x[1], reverse=True)
        description += "_Node Operators:_ "
        description += ", ".join([f"{count}x {el_explorer_url(node_operator)}" for node_operator, count in node_operators])
        return description


lottery = LotteryBase()


class Lottery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def lottery(self, ctx: Context):
        """
        Get the status of the current and next sync committee.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(title="Sync Committee Lottery")
        description = "**Current sync committee:**\n"
        description += await lottery.generate_sync_committee_description("latest")
        description += "\n\n"
        description += "**Next sync committee:**\n"
        description += await lottery.generate_sync_committee_description("next")
        e.description = description

        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Lottery(bot))
