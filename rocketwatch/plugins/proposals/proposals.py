import asyncio
import logging
import random
import time
from io import BytesIO

import aiohttp
from discord import Embed, Color, File
from discord.commands import slash_command
from discord.ext import commands
from matplotlib import pyplot as plt
from motor.motor_asyncio import AsyncIOMotorClient
from wordcloud import WordCloud

from utils.cfg import cfg
from utils.rocketpool import rp
from utils.slash_permissions import guilds, owner_only_slash
from utils.visibility import is_hidden

log = logging.getLogger("proposals")
log.setLevel(cfg["log_level"])

LOOKUP = {
    "N": "Nimbus",
    "P": "Prysm",
    "L": "Lighthouse",
    "T": "Teku"
}

COLORS = {
    "Nimbus"    : "#cc9133",
    "Prysm"     : "#40bfbf",
    "Lighthouse": "#9933cc",
    "Teku"      : "#3357cc",
    "Smart Node": "#cc6e33",
    "Allnodes"  : "#4533cc",
    "Unobserved": "#E0E0E0",
    "N/A"       : "#999999",
}


class Proposals(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)
        self.rocketscan_proposals_url = "https://rocketscan.dev/api/mainnet/beacon/blocks/all"
        self.last_chore_run = 0
        self.validator_url = "https://beaconcha.in/api/v1/validator/"
        # connect to local mongodb
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @owner_only_slash()
    async def drop_proposals(self, ctx):
        await ctx.defer()
        log.info("dropping all proposals")
        await self.db.proposals.delete_many({})
        log.info("finished dropping all proposals")
        await ctx.respond("dropped all proposals")

    @owner_only_slash()
    async def drop_node_operators(self, ctx):
        await ctx.defer()
        log.info("dropping all node operators")
        await self.db.node_operators.delete_many({})
        log.info("finished dropping node operators")
        await ctx.respond("dropped all node operators")

    async def gather_all_proposals(self):
        log.info("getting all proposals using the rocketscan.dev API")
        async with aiohttp.ClientSession() as session:
            async with session.get(self.rocketscan_proposals_url) as resp:
                if resp.status != 200:
                    log.error("failed to get proposals using the rocketscan.dev API")
                    return

                proposals = await resp.json()
                log.info("got all proposals using the rocketscan.dev API")
                for entry in proposals:
                    validator = int(entry["validator"]["index"])
                    slot = int(entry["number"])
                    graffiti = bytes.fromhex(entry["validator"]["graffiti"][2:]).decode("utf-8").rstrip('\x00')
                    if graffiti.startswith("RP-"):
                        # smart node proposal
                        parts = graffiti.split(" ")
                        data = {
                            "slot"     : slot,
                            "validator": validator,
                            "client"   : LOOKUP.get(parts[0].split("-")[1], "N/A"),
                            "version"  : parts[1],
                            "comment"  : " ".join(parts[2:]).lstrip("(").rstrip(")"),
                            "type"     : "Smart Node",
                        }
                    elif "⚡️Allnodes" in graffiti:
                        # Allnodes proposal
                        data = {
                            "slot"     : slot,
                            "validator": validator,
                            "client"   : "Teku",  # could change in the future
                            "type"     : "Allnodes"
                        }
                    else:
                        # normal proposal
                        data = {
                            "slot"     : slot,
                            "validator": validator,
                            "type"     : "N/A",
                            "client"   : "N/A"
                        }
                        for client in LOOKUP.values():
                            if client.lower() in graffiti.lower():
                                data["client"] = client
                                break
                    # add it to the db if its not already there
                    await self.db.proposals.update_one(
                        {"slot": slot}, {"$set": data}, upsert=True
                    )
        log.info("finished gathering all proposals")

    async def lookup_validators(self):
        log.info("looking up new validators")
        # get all validators that arent in the node_operators collection
        validators = await self.db.proposals.aggregate([
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
                node_operator = rp.call("rocketMinipool.getNodeAddress", address=minipool)
                # get node operator
                await self.db.node_operators.replace_one({"validator": validator_id},
                                                         {"validator"    : validator_id,
                                                          "pubkey"       : pubkey,
                                                          "node_operator": node_operator},
                                                         upsert=True)
            await asyncio.sleep(10)
        log.info("finished looking up new validators")

    async def chore(self, ctx):
        msg = await ctx.respond("doing chores...", ephemeral=is_hidden(ctx))
        # only run if self.last_chore_run timestamp is older than 1 hour
        if (time.time() - self.last_chore_run) > 3600:
            self.last_chore_run = time.time()
            await msg.edit(content="gathering proposals...")
            await self.gather_all_proposals()
            await msg.edit(content="looking up new validators...")
            await self.lookup_validators()
        else:
            log.debug("skipping chore")
        return msg

    async def gather_attribute_per_validator(self, attribute):
        distribution = await self.db.proposals.aggregate([
            {
                "$match": {
                    attribute: {"$exists": True}
                }
            }, {
                '$sort': {
                    'slot': -1
                }
            }, {
                '$group': {
                    '_id'     : '$validator',
                    'proposal': {
                        '$first': '$$ROOT'
                    }
                }
            }, {
                '$project': {
                    'attribute': '$proposal.' + attribute
                }
            }, {
                '$group': {
                    '_id'  : '$attribute',
                    'count': {
                        '$sum': 1
                    },
                }
            }, {
                '$sort': {
                    'count': 1
                }
            }
        ]).to_list(length=None)

        return distribution

    async def gather_attributel_per_node_operator(self, attribute):
        distribution = await self.db.node_operators.aggregate([
            {
                '$match': {
                    'node_operator': {
                        '$ne': None
                    }
                }
            },
            # get the proposals per validator
            {
                '$lookup': {
                    'from'        : 'proposals',
                    'localField'  : 'validator',
                    'foreignField': 'validator',
                    'as'          : 'proposals',
                    'pipeline'    : [
                        {
                            '$sort': {
                                'slot': -1
                            }
                        },
                        {
                            "$match": {
                                attribute: {"$exists": True}
                            }
                        },
                    ]
                }
                # remove validators that have no proposals
            }, {
                '$match': {
                    'proposals': {
                        '$ne': []
                    }
                }
                # only keep the latest one
            }, {
                '$project': {
                    'node_operator': 1,
                    'validator'    : 1,
                    'proposal'     : {
                        '$arrayElemAt': [
                            '$proposals', 0
                        ]
                    }
                }
                # extract slot from proposal
            }, {
                '$project': {
                    'node_operator': 1,
                    'validator'    : 1,
                    'slot'         : '$proposal.slot'
                }
                # group by node_operator, keep the latest slot
            }, {
                '$group': {
                    '_id' : '$node_operator',
                    'slot': {
                        '$max': '$slot'
                    }
                }
                # get the proposals per node_operator using the latest slot
            }, {
                '$lookup': {
                    'from'        : 'proposals',
                    'localField'  : 'slot',
                    'foreignField': 'slot',
                    'as'          : 'proposal'
                }
                # only keep the latest proposal
            }, {
                '$project': {
                    'node_operator': 1,
                    'proposal'     : {
                        '$arrayElemAt': [
                            '$proposal', 0
                        ]
                    }
                }

            }, {
                '$project': {
                    'attribute': '$proposal.' + attribute
                }
            }, {
                '$group': {
                    '_id'  : '$attribute',
                    'count': {
                        '$sum': 1
                    },
                }
            }, {
                '$sort': {
                    'count': 1
                }
            }
        ]).to_list(length=None)
        return distribution

    @slash_command(guild_ids=guilds)
    async def version_chart(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating version chart...")

        e = Embed(title="Version Chart", color=self.color)

        # get proposals
        proposals = await self.db.proposals.find({"version": {"$exists": 1}}).sort("slot", 1).to_list(None)
        batch_size = int(60 / 12 * 60 * 24 * 2)
        data = {}
        versions = []
        for proposal in proposals:
            slot = proposal["slot"] // batch_size * batch_size
            if slot not in data:
                data[slot] = {}
            if proposal["version"] not in data[slot]:
                data[slot][proposal["version"]] = 0
            if proposal["version"] not in versions:
                versions.append(proposal["version"])
            data[slot][proposal["version"]] += 1

        latest_slot = int(max(data.keys()))
        versions_from_latest = [x for x in versions if x in data[latest_slot]]
        # show stats from the latest batch
        descriptions = [
            f"{version}: {data[latest_slot][version]}" for version in versions_from_latest
        ]

        descriptions = "```\n" + "\n".join(descriptions) + "```"
        e.add_field(name=f"Statistics for slots {latest_slot} - {latest_slot + batch_size}", value=descriptions)

        # normalize data
        for slot, value in data.items():
            total = sum(data[slot].values())
            for version in data[slot]:
                value[version] /= total

        # use plt.stackplot to stack the data
        x = list(data.keys())
        y = {v: [] for v in versions}
        for slot, value_ in data.items():
            for version in versions:
                y[version].append(value_.get(version, 0))

        plt.stackplot(x, *y.values(), labels=versions)
        plt.title("Version Chart")
        plt.xlabel("slot")
        plt.ylabel("Percentage")
        plt.legend(loc="upper left")
        plt.tight_layout()

        # respond with image
        img = BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        e.set_image(url="attachment://chart.png")

        # send data
        await msg.edit(content="", embed=e, file=File(img, filename="chart.png"))
        img.close()

    async def proposal_vs_node_operators_embed(self, attribute, name, msg):
        await msg.edit(content=f"generating {name} distribution graph...")

        e = Embed(title="Client Distribution", color=self.color)

        # group by client and get count
        minipools = await self.gather_attribute_per_validator(attribute)
        minipools = [list(x.values()) for x in minipools]

        # create description
        descriptions = ["Minipool Counts:"]
        descriptions += [
            f"\t{d[0]}: {d[1]}" for d in reversed(minipools)
        ]

        descriptions = "```\n" + "\n".join(descriptions) + "```"
        e.description = descriptions

        # get total minipool count from rocketpool
        unobserved_minipools = rp.call("rocketMinipoolManager.getStakingMinipoolCount") - sum(d[1] for d in minipools)
        minipools.insert(0, ("Unobserved", unobserved_minipools))

        # get node operators
        node_operators = await self.gather_attributel_per_node_operator(attribute)
        node_operators = [list(x.values()) for x in node_operators]

        # get total node operator count from rp
        unobserved_node_operators = rp.call("rocketNodeManager.getNodeCount") - sum(d[1] for d in node_operators)

        # sort data
        node_operators.insert(0, ("Unobserved", unobserved_node_operators))

        # create 2 subplots
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 8))
        fig.subplots_adjust(left=0, right=1, top=0.9, bottom=0, wspace=0)
        plt.rcParams.update({'font.size': 15})

        def my_autopct(pct):
            return ('%.1f%%' % pct) if pct > 3 else ''

        ax1.pie(
            [x[1] for x in minipools],
            colors=[COLORS[x[0]] for x in minipools],
            autopct=my_autopct,
            startangle=90
        )
        # legend
        total_minipols = sum([x[1] for x in minipools])
        ax1.legend(
            [f"{x[1]} {x[0]} ({x[1] / total_minipols:.2%})" for x in minipools],
            loc="upper left",
        )
        ax1.set_title(f"{name} Distribution based on Minipools")

        ax2.pie(
            [x[1] for x in node_operators],
            colors=[COLORS[x[0]] for x in node_operators],
            autopct=my_autopct,
            startangle=90
        )
        # legend
        total_node_operators = sum([x[1] for x in node_operators])
        ax2.legend(
            [f"{x[1]} {x[0]} ({x[1] / total_node_operators:.2%})" for x in node_operators],
            loc="upper left"
        )
        ax2.set_title(f"{name} Distribution based on Node Operators")

        # respond with image
        img = BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        plt.rcParams.update({'font.size': 10})
        e.set_image(url="attachment://chart.png")

        # send data
        await msg.edit(content="", embed=e, file=File(img, filename="chart.png"))
        img.close()

    @slash_command(guild_ids=guilds)
    async def client_distribution(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await self.proposal_vs_node_operators_embed("client", "Client", msg)

    @slash_command(guild_ids=guilds)
    async def user_distribution(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await self.proposal_vs_node_operators_embed("type", "User", msg)

    @slash_command(guild_ids=guilds)
    async def comments(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating comments word cloud...")

        # aggregate comments with their count
        comments = await self.db.proposals.aggregate([
            {"$match": {"comment": {"$exists": 1}}},
            {"$group": {"_id": "$comment", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]).to_list(None)
        comment_words = {x['_id']: x["count"] for x in comments}
        wordcloud = WordCloud(width=800,
                              height=400,
                              margin=10,
                              background_color="white",
                              prefer_horizontal=0.9,
                              # color func for random color
                              color_func=lambda *args, **kwargs: list(COLORS.values())[random.randint(0, len(COLORS) - 3)]
                              ).fit_words(comment_words)

        # respond with image
        img = BytesIO()
        wordcloud.to_image().save(img, format="png")
        img.seek(0)
        plt.close()
        e = Embed(title="Rocket Pool Proposal Comments", color=self.color)
        e.set_image(url="attachment://image.png")
        await msg.edit(content="", embed=e, file=File(img, filename="image.png"))
        img.close()


def setup(bot):
    bot.add_cog(Proposals(bot))
