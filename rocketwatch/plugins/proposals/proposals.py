import html
import random

import asyncio
import logging
from io import BytesIO

import aiohttp
import matplotlib
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

CLIENTS = {
    "N": "Nimbus",
    "P": "Prysm",
    "L": "Lighthouse",
    "T": "Teku"
}

COLORS = {
    "Nimbus": "#cc9133",
    "Prysm": "#40bfbf",
    "Lighthouse": "#9933cc",
    "Teku": "#3357cc",
    "unknown": "#B0B0B0",
}


class Proposals(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)
        self.slots_url = "https://beaconcha.in/blocks/data"
        self.validator_url = "https://beaconcha.in/api/v1/validator/"
        # connect to local mongodb
        self.db = AsyncIOMotorClient("mongodb://mongodb:27017").get_database("rocketwatch")

    @owner_only_slash()
    async def drop_proposals(self, ctx):
        await ctx.defer()
        log.info("dropping all proposals")
        await self.db.proposals.delete_many({})
        log.info("finished dropping all proposals")
        await ctx.respond("dropped all proposals")

    async def gather_new_proposals(self):
        log.info("getting proposals with rocket pool graffiti...")
        amount = 100
        index = 0
        start = 0
        should_continue = True
        while True:
            log.debug(f"requesting proposals {start} to {start + amount}")
            async with aiohttp.ClientSession() as session:
                res = await session.get(
                    self.slots_url,
                    params={
                        "draw"         : index,
                        "start"        : start,
                        "length"       : amount,
                        "search[value]": "RP-"
                    })
                res = await res.json()
            proposals = res["data"]
            for entry in proposals:
                slot = int(entry[1].split(">")[-2].split("<")[0].replace(",", ""))

                # break if the slot is already in the database
                if await self.db.proposals.count_documents({"slot": slot}) > 0:
                    log.debug(f"slot {slot} already in the database")
                    should_continue = False
                    continue

                graffiti = entry[11].split(">")[-3].split("<")[0]
                if graffiti.startswith("RP-"):
                    should_continue = True
                    parts = graffiti.split(" ")
                    data = {
                        "slot"     : slot,
                        "validator": int(entry[4].split("/validator/")[1].split("\">")[0]),
                        "client"   : parts[0].split("-")[1], "version": parts[1]
                    }
                    if len(parts) > 2:
                        comment = " ".join(parts[2:]).lstrip("(").rstrip(")")
                        data["comment"] = html.unescape(comment)
                    await self.db.proposals.replace_one({"slot": data["slot"]}, data, upsert=True)

            if len(proposals) != 100 or not should_continue:
                log.debug(f"stopping proposal gathering: {len(proposals)=}, {should_continue=}")
                break
            index += 1
            start += amount
            await asyncio.sleep(5)
        log.info("finished gathering new proposals")

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
                    'from': 'node_operators',
                    'localField': '_id',
                    'foreignField': 'validator',
                    'as': 'data'
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
            for validator_id, validator_data in zip(validator_ids, data):
                # look up pubkey in rp
                pubkey = validator_data["pubkey"]
                # get minipool address
                minipool = rp.call("rocketMinipoolManager.getMinipoolByPubkey", pubkey)
                # get node operator
                node_operator = rp.call("rocketMinipool.getNodeAddress", address=minipool)
                # store (validator, pubkey, node_operator) in node_operators collection
                await self.db.node_operators.replace_one({"validator": int(validator_id)},
                                                         {"validator"    : int(validator_id),
                                                          "pubkey"       : pubkey,
                                                          "node_operator": node_operator},
                                                         upsert=True)
            await asyncio.sleep(5)
        log.info("finished looking up new validators")

    async def chore(self, ctx):
        msg = await ctx.respond("gathering new proposals...", ephemeral=is_hidden(ctx))
        await self.gather_new_proposals()
        await msg.edit(content="looking up new validators...")
        await self.lookup_validators()
        return msg

    async def gather_latest_proposal_per_node_operator(self):
        data = await self.db.node_operators.aggregate([
            # get the proposals per validator
            {
                '$lookup': {
                    'from': 'proposals',
                    'localField': 'validator',
                    'foreignField': 'validator',
                    'as': 'proposals',
                    'pipeline': [
                        {
                            '$sort': {
                                'slot': -1
                            }
                        }
                    ]
                }
                # only keep the latest one
            }, {
                '$project': {
                    'node_operator': 1,
                    'validator': 1,
                    'proposal': {
                        '$arrayElemAt': [
                            '$proposals', 0
                        ]
                    }
                }
                # extract slot from proposal
            }, {
                '$project': {
                    'node_operator': 1,
                    'validator': 1,
                    'slot': '$proposal.slot'
                }
                # group by node_operator, keep the latest slot
            }, {
                '$group': {
                    '_id': '$node_operator',
                    'slot': {
                        '$max': '$slot'
                    }
                }
                # get the proposals per node_operator using the latest slot
            }, {
                '$lookup': {
                    'from': 'proposals',
                    'localField': 'slot',
                    'foreignField': 'slot',
                    'as': 'proposal'
                }
                # only keep the latest proposal
            }, {
                '$project': {
                    'node_operator': 1,
                    'proposal': {
                        '$arrayElemAt': [
                            '$proposal', 0
                        ]
                    }
                }
                # extract the proposal metadata
            }, {
                '$project': {
                    'node_operator': 1,
                    'slot': '$proposal.slot',
                    'client': '$proposal.client',
                    'version': '$proposal.version',
                    'comment': '$proposal.comment'
                }
            }
        ]).to_list(length=None)
        return data

    @slash_command(guild_ids=guilds)
    async def version_chart(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating version chart...")

        e = Embed(title="Version Chart", color=self.color)

        # get proposals
        proposals = await self.db.proposals.find().sort("slot", 1).to_list(None)
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

    @slash_command(guild_ids=guilds)
    async def client_distribution(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating client distribution graph...")

        e = Embed(title="Client Distribution", color=self.color)

        # get proposals
        proposals = await self.db.proposals.find().sort("slot", 1).to_list(None)

        # get the total client distribution
        total_data = {}
        for proposal in proposals:
            client = CLIENTS.get(proposal["client"], "unknown")
            if client not in total_data:
                total_data[client] = 0
            total_data[client] += 1

        # sort data
        total_data = sorted(total_data.items(), key=lambda x: x[1])

        # get node operators
        node_operators = await self.gather_latest_proposal_per_node_operator()

        # get total node operator count from rp
        unknown_count = rp.call("rocketNodeManager.getNodeCount") - len(node_operators)

        # get the node operator client distribution
        node_operator_data = {}
        for proposal in node_operators:
            client = CLIENTS.get(proposal["client"], "unknown")
            if client not in node_operator_data:
                node_operator_data[client] = 0
            node_operator_data[client] += 1

        # sort data
        node_operator_data = sorted(node_operator_data.items(), key=lambda x: x[1])
        node_operator_data.insert(0, ("unknown", unknown_count))

        # create description
        descriptions = [
            f"{d[0]}: {d[1]}" for d in reversed(total_data)
        ]

        descriptions = "```\n" + "\n".join(descriptions) + "```"
        e.description = descriptions

        # create 2 subplots
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))

        ax1.pie(
            [x[1] for x in total_data],
            colors=[COLORS[x[0]] for x in total_data],
            autopct="%1.1f%%",
            startangle=90
        )
        # legend
        ax1.legend(
            [f"{x[0]} ({x[1]})" for x in total_data],
            loc="upper left",
        )
        ax1.set_title("Client Distribution based on Proposals")

        ax2.pie(
            [x[1] for x in node_operator_data],
            colors=[COLORS[x[0]] for x in node_operator_data],
            autopct="%1.1f%%",
            startangle=90
        )
        # legend
        ax2.legend(
            [f"{x[0]} ({x[1]})" for x in node_operator_data],
            loc="upper left"
        )
        ax2.set_title("Client Distribution based on Node Operators")

        # respond with image
        img = BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        e.set_image(url="attachment://chart.png")

        # send data
        await msg.edit(content="", embed=e, file=File(img, filename="chart.png"))
        img.close()

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
                              color_func=lambda *args, **kwargs: list(COLORS.values())[random.randint(0, len(COLORS) - 2)]
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
