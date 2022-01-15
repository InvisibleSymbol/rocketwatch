import logging

import aiohttp
from discord.commands import slash_command
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReplaceOne

from utils.cfg import cfg
from utils.embeds import Embed
from utils.embeds import etherscan_url
from utils.readable import beaconchain_url
from utils.slash_permissions import guilds
from utils.solidity import BEACON_START_DATE, BEACON_EPOCH_LENGTH
from utils.visibility import is_hidden

log = logging.getLogger("proposals")
log.setLevel(cfg["log_level"])


class Lottery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.endpoint = "https://beaconcha.in/api/v1/sync_committee"
        self.validator_url = "https://beaconcha.in/api/v1/validator/"
        # connect to local mongodb
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    async def load_sync_committee(self, period):
        async with aiohttp.ClientSession() as session:
            res = await session.get("/".join([self.endpoint, period]))
            res = await res.json()
        data = res["data"]
        self.db.sync_committee_stats.replace_one({"period": period},
                                                 {"period"     : period,
                                                  "start_epoch": data["start_epoch"],
                                                  "end_epoch"  : data["end_epoch"],
                                                  "sync_period": data["period"],
                                                  }, upsert=True)
        validators = data["validators"]
        col = self.db["sync_committee_" + period]
        payload = [
            ReplaceOne(
                {"index": i}, {"index": i, "validator": validator}, upsert=True
            )
            for i, validator in enumerate(validators)
        ]

        await col.bulk_write(payload)
        return

    async def chore(self, ctx):
        msg = await ctx.respond("loading latest sync committee...", ephemeral=is_hidden(ctx))
        await self.load_sync_committee("latest")
        await msg.edit(content="loading next sync committee...")
        await self.load_sync_committee("next")
        return msg

    async def get_validators_for_sync_committee_period(self, period):
        data = await self.db.minipools.aggregate([
            # filter out validators that have no node operator
            {
                '$match': {
                    'node_operator': {
                        '$ne': None
                    }
                }
            },
            # get the sync committee entry per node operator
            {
                '$lookup': {
                    'from'        : f'sync_committee_{period}',
                    'localField'  : 'validator',
                    'foreignField': 'validator',
                    'as'          : 'entry',
                    'pipeline'    : [
                        {
                            '$sort': {
                                'slot': -1
                            }
                        }
                    ]
                }
            },
            # remove validators that are not in the sync committee
            {
                '$match': {
                    'entry': {
                        '$ne': []
                    }
                }
            },
            {
                '$project': {
                    '_id'          : 0,
                    'validator'    : 1,
                    'pubkey'       : 1,
                    'node_operator': 1

                }
            }]).to_list(length=None)

        return data

    async def generate_sync_committee_description(self, period):
        validators = await self.get_validators_for_sync_committee_period(period)
        # get stats about the current period
        stats = await self.db.sync_committee_stats.find_one({"period": period})
        perc = len(validators) / 512
        description = f"Rocket Pool Participation: {len(validators)}/512 ({perc:.2%})\n"
        start_timestamp = BEACON_START_DATE + (stats['start_epoch'] * BEACON_EPOCH_LENGTH)
        description += f"Start: Epoch {stats['start_epoch']} <t:{start_timestamp}> (<t:{start_timestamp}:R>)\n"
        end_timestamp = BEACON_START_DATE + (stats['end_epoch'] * BEACON_EPOCH_LENGTH)
        description += f"End: Epoch {stats['end_epoch']} <t:{end_timestamp}> (<t:{end_timestamp}:R>)\n"
        # validators (called minipools here)
        description += f"Minipools: {', '.join(beaconchain_url(v['pubkey']) for v in validators)}\n"
        # node operators
        # gather count per
        node_operators = {}
        for v in validators:
            if v['node_operator'] not in node_operators:
                node_operators[v['node_operator']] = 0
            node_operators[v['node_operator']] += 1
        # sort by count
        node_operators = sorted(node_operators.items(), key=lambda x: x[1], reverse=True)
        description += "Node Operators: "
        description += f", ".join([f"{count}x {etherscan_url(node_operator)}" for node_operator, count in node_operators])
        return description

    @slash_command(guild_ids=guilds)
    async def lottery(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating lottery embed...")
        e = Embed(title="Sync Committee Lottery")
        description = ""
        description += "**Current sync committee:**\n"
        description += await self.generate_sync_committee_description("latest")
        description += "\n\n"
        description += "**Next sync committee:**\n"
        description += await self.generate_sync_committee_description("next")
        e.description = description

        await msg.edit(content="", embed=e)


def setup(bot):
    bot.add_cog(Lottery(bot))
