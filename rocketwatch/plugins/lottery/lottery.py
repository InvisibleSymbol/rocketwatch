import asyncio
import logging

import aiohttp
from discord import Embed, Color
from discord.commands import slash_command
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import etherscan_url
from utils.readable import beaconchain_url
from utils.rocketpool import rp
from utils.slash_permissions import guilds
from utils.solidity import BEACON_START_DATE, BEACON_EPOCH_LENGTH
from utils.visibility import is_hidden

log = logging.getLogger("proposals")
log.setLevel(cfg["log_level"])


class Lottery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)
        self.endpoint = "https://beaconcha.in/api/v1/sync_committee"
        self.validator_url = "https://beaconcha.in/api/v1/validator/"
        # connect to local mongodb
        self.db = AsyncIOMotorClient("mongodb://mongodb:27017").get_database("rocketwatch")

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
        for i, validator in enumerate(validators):
            await col.replace_one({"index": i}, {"index": i, "validator": validator}, upsert=True)
        return

    async def lookup_validators(self, period):
        log.info("looking up new validators")
        col = self.db["sync_committee_" + period]
        # get all validators that arent in the node_operators collection
        validators = await col.aggregate([
            {
                '$group': {
                    '_id': '$validator'
                }
            }, {
                '$lookup': {
                    'from'        : 'node_operators',
                    'localField'  : '_id',
                    'foreignField': 'validator',
                    'as'          : 'data'
                }
            }, {
                '$match': {
                    'data': {
                        '$size': 0
                    }
                }
            }
        ]).to_list(length=None)
        validators = [str(x["_id"]) for x in validators]
        # filter out validators that are already in the
        for i in range(0, len(validators), 100):
            log.debug(f"requesting pubkeys {i} to {i + 100}")
            validator_ids = validators[i:i + 100]
            async with aiohttp.ClientSession() as session:
                res = await session.get(self.validator_url + ",".join(validator_ids))
                res = await res.json()
            data = res["data"]
            # handle when we only get a single validator back
            if not isinstance(data, list):
                data = [data]
            for validator_data in data:
                validator_id = int(validator_data["validatorindex"])
                # look up pubkey in rp
                pubkey = validator_data["pubkey"]
                # get minipool address
                minipool = rp.call("rocketMinipoolManager.getMinipoolByPubkey", pubkey)
                if int(minipool[2:], 16) == 0:
                    node_operator = None
                else:
                    node_operator = rp.call("rocketMinipool.getNodeAddress", address=minipool)
                # get node operator
                # store (validator, pubkey, node_operator) in node_operators collection
                await self.db.node_operators.replace_one({"validator": validator_id},
                                                         {"validator"    : validator_id,
                                                          "pubkey"       : pubkey,
                                                          "node_operator": node_operator},
                                                         upsert=True)
            await asyncio.sleep(10)
        log.info("finished looking up new validators")

    async def chore(self, ctx):
        msg = await ctx.respond("loading latest sync committee...", ephemeral=is_hidden(ctx))
        await self.load_sync_committee("latest")
        await msg.edit(content="looking up validators for latest sync committee...")
        await self.lookup_validators("latest")
        await msg.edit(content="loading next sync committee...")
        await self.load_sync_committee("next")
        await msg.edit(content="looking up validators for next sync committee...")
        await self.lookup_validators("next")
        return msg

    async def get_validators_for_sync_committee_period(self, period):
        data = await self.db.node_operators.aggregate([
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
        e = Embed(title="Sync Committee Lottery", color=self.color)
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
