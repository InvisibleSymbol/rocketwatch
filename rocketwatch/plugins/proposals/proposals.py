import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import logging
import re
import time
from io import BytesIO

import aiohttp
import matplotlib as mpl
import numpy as np
from PIL import Image
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from matplotlib import pyplot as plt
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReplaceOne
from wordcloud import WordCloud

from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.solidity import beacon_block_to_date, date_to_beacon_block
from utils.time_debug import timerun, timerun_async
from utils.visibility import is_hidden

log = logging.getLogger("proposals")
log.setLevel(cfg["log_level"])

LOOKUP = {
    "consensus": {
        "N": "Nimbus",
        "P": "Prysm",
        "L": "Lighthouse",
        "T": "Teku",
        "S": "Lodestar"
    },
    "execution": {
        "I": "Infura",
        "P": "Pocket",
        "G": "Geth",
        "B": "Besu",
        "N": "Nethermind",
        "X": "External"
    }
}

COLORS = {
    "Nimbus"          : "#cc9133",
    "Prysm"           : "#40bfbf",
    "Lighthouse"      : "#9933cc",
    "Teku"            : "#3357cc",

    "Infura"          : "#ff2f00",
    "Pocket"          : "#e216e9",
    "Geth"            : "#40bfbf",
    "Besu"            : "#55aa7a",
    "Nethermind"      : "#2688d9",
    "External"        : "#808080",

    "Smart Node"      : "#cc6e33",
    "Allnodes"        : "#4533cc",
    "No proposals yet": "#E0E0E0",
    "Unknown"         : "#AAAAAA",
}

PROPOSAL_TEMPLATE = {
    "type"            : "Unknown",
    "consensus_client": "Unknown",
    "execution_client": "Unknown",
}

# noinspection RegExpUnnecessaryNonCapturingGroup
SMARTNODE_REGEX = re.compile(r"^RP(?:(?:-)([A-Z])([A-Z])?)? (?:v)?(\d+\.\d+\.\d+(?:-\w+)?)(?:(?:(?: \()|(?: gw:))(.+)(?:\)))?")


def parse_propsal(entry):
    graffiti = bytes.fromhex(entry["validator"]["graffiti"][2:]).decode("utf-8").rstrip('\x00')
    data = {
        "slot"     : int(entry["number"]),
        "validator": int(entry["validator"]["index"]),
        "graffiti" : graffiti,
    }
    if m := SMARTNODE_REGEX.findall(graffiti):
        groups = m[0]
        # smart node proposal
        data["type"] = "Smart Node"
        data["version"] = groups[2]
        if groups[1]:
            data["consensus_client"] = LOOKUP["consensus"].get(groups[1], "Unknown")
            data["execution_client"] = LOOKUP["execution"].get(groups[0], "Unknown")
        elif groups[0]:
            data["consensus_client"] = LOOKUP["consensus"].get(groups[0], "Unknown")
        if groups[3]:
            data["comment"] = groups[3]
    elif "⚡️Allnodes" in graffiti:
        # Allnodes proposal
        data["type"] = "Allnodes"
        data["consensus_client"] = "Teku"
        data["execution_client"] = "Geth"
    else:
        # normal proposal
        # try to detect the client from the graffiti
        graffiti = graffiti.lower()
        for client in LOOKUP["consensus"].values():
            if client.lower() in graffiti:
                data["consensus_client"] = client
                break
        for client in LOOKUP["execution"].values():
            if client.lower() in graffiti:
                data["execution_client"] = client
                break
    return data


class Proposals(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rocketscan_proposals_url = "https://rocketscan.io/api/mainnet/beacon/blocks/all"
        self.last_chore_run = 0
        # connect to local mongodb
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.created_view = False

    async def create_minipool_proposal_view(self):
        if self.created_view:
            return
        log.info("creating minipool proposal view")
        pipeline = [
            {
                '$match': {
                    'node_operator': {
                        '$ne': None
                    }
                }
            }, {
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
                        }
                    ]
                }
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
            }, {
                '$project': {
                    'node_operator': 1,
                    'validator'    : 1,
                    'slot'         : '$proposal.slot'
                }
            }, {
                '$group': {
                    '_id'            : '$node_operator',
                    'slot'           : {
                        '$max': '$slot'
                    },
                    'validator_count': {
                        '$sum': 1
                    }
                }
            }, {
                '$match': {
                    'slot': {
                        '$ne': None
                    }
                }
            }, {
                '$lookup': {
                    'from'        : 'proposals',
                    'localField'  : 'slot',
                    'foreignField': 'slot',
                    'as'          : 'proposals'
                }
            }, {
                '$project': {
                    'node_operator'  : 1,
                    'latest_proposal': {
                        '$arrayElemAt': [
                            '$proposals', 0
                        ]
                    },
                    'validator_count': 1
                }
            }
        ]
        await self.db.minipool_proposals.drop()
        await self.db.create_collection(
            "minipool_proposals",
            viewOn="minipools",
            pipeline=pipeline
        )
        self.created_view = True

    async def gather_all_proposals(self):
        log.info("getting all proposals using the rocketscan.dev API")
        async with aiohttp.ClientSession() as session:
            async with session.get(self.rocketscan_proposals_url) as resp:
                if resp.status != 200:
                    log.error("failed to get proposals using the rocketscan.dev API")
                    return
                proposals = await resp.json()
        log.info("got all proposals using the rocketscan.dev API")
        await self.db.proposals.bulk_write([ReplaceOne({"slot": int(entry["number"])},
                                                       PROPOSAL_TEMPLATE | parse_propsal(entry),
                                                       upsert=True) for entry in proposals])
        log.info("finished gathering all proposals")

    async def chore(self, ctx: Context):
        # only run if self.last_chore_run timestamp is older than 1 hour
        msg = await ctx.send(content="doing chores...")
        if (time.time() - self.last_chore_run) > 3600:
            self.last_chore_run = time.time()
            await msg.edit(content="gathering proposals...")
            await self.gather_all_proposals()
            await self.create_minipool_proposal_view()
        else:
            log.debug("skipping chore")
        return msg

    @timerun_async
    async def gather_attribute(self, attribute, remove_allnodes=False):
        distribution = await self.db.minipool_proposals.aggregate([
            {
                '$project': {
                    'attribute'      : f'$latest_proposal.{attribute}',
                    'type'           : '$latest_proposal.type',
                    'validator_count': 1
                }
            }, {
                '$group': {
                    '_id'            : ['$attribute', '$type'],
                    'count'          : {
                        '$sum': 1
                    },
                    'validator_count': {
                        '$sum': '$validator_count'
                    }
                }
            }, {
                '$sort': {
                    'count': 1
                }
            }
        ]).to_list(length=None)
        if remove_allnodes:
            d = {'remove_from_total': {'count': 0, 'validator_count': 0}}
            for entry in distribution:
                if entry['_id'][1] == 'Allnodes':
                    d['remove_from_total']['count'] += entry['count']
                    d['remove_from_total']['validator_count'] += entry['validator_count']
                else:
                    d[entry['_id'][0]] = entry
            return d
        else:
            distribution = [entry | {'_id': entry['_id'][0]} for entry in distribution]
            # merge entries that have the same _id by summing their attributes
            d = {}
            for entry in distribution:
                if entry["_id"] in d:
                    d[entry["_id"]]["count"] += entry["count"]
                    d[entry["_id"]]["validator_count"] += entry["validator_count"]
                else:
                    d[entry["_id"]] = entry
        return d

    @hybrid_command()
    async def version_chart(self, ctx: Context):
        """
        Show a historical chart of used Smart Node versions
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating version chart...")

        e = Embed(title="Version Chart")
        e.description = "The graph below shows proposal stats using a **5-day rolling window**, " \
                        "and **does not represent operator adoption**.\n" \
                        "Versions with a proposal in the **last 2 days** are emphasized.\n\n" \
                        "The percentages in the top left legend show the percentage of proposals observed in the last 5 days using that version.\n" \
                        "**If an old version is shown as 10%, it means that it was 10% of the proposals in the last 5 days.**\n" \
                        "_No it does not mean that the minipools simply haven't proposed with the new version yet._\n" \
                        "This only looks at proposals, it does not care about what individual minipools do."
        # get proposals
        # limit to 6 months
        proposals = await self.db.proposals.find(
            {
                "version": {"$exists": 1},
                "slot"   : {"$gt": date_to_beacon_block((datetime.now() - timedelta(days=180)).timestamp())}
            }).sort("slot", 1).to_list(None)
        look_back = int(60 / 12 * 60 * 24 * 2)  # last 2 days
        max_slot = proposals[-1]["slot"]
        # get version used after max_slot - look_back
        # and have at least 10 occurrences
        start_slot = max_slot - look_back
        recent_versions = await self.db.proposals.aggregate([
            {
                '$match': {
                    'slot'   : {
                        '$gte': start_slot
                    },
                    'version': {
                        '$exists': 1
                    }
                }
            }, {
                '$group': {
                    '_id'  : '$version'
                }
            }, {
                '$sort': {
                    '_id': 1
                }
            }
        ]).to_list(None)
        recent_versions = [v['_id'] for v in recent_versions]
        data = {}
        versions = []
        proposal_buffer = []
        tmp_data = {}
        for i, proposal in enumerate(proposals):
            proposal_buffer.append(proposal)
            if proposal["version"] not in versions:
                versions.append(proposal["version"])
            tmp_data[proposal["version"]] = tmp_data.get(proposal["version"], 0) + 1
            slot = proposal["slot"]
            if i < 200:
                continue
            while proposal_buffer[0]["slot"] < slot - (60 / 12 * 60 * 24 * 5):
                to_remove = proposal_buffer.pop(0)
                tmp_data[to_remove["version"]] -= 1
            date = datetime.fromtimestamp(beacon_block_to_date(slot))
            data[date] = tmp_data.copy()

        # normalize data
        for date, value in data.items():
            total = sum(data[date].values())
            for version in data[date]:
                value[version] /= total

        # use plt.stackplot to stack the data
        x = list(data.keys())
        y = {v: [] for v in versions}
        for date, value_ in data.items():
            for version in versions:
                y[version].append(value_.get(version, 0))

        # matplotlib default color
        matplotlib_colors = [color['color'] for color in list(mpl.rcParams['axes.prop_cycle'])]
        # cap recent versions to available colors, but we want to prioritize the most recent versions
        recent_versions = recent_versions[-len(matplotlib_colors):]
        recent_colors = [matplotlib_colors[i] for i in range(len(recent_versions))]
        # generate color mapping
        colors = ["darkgray"] * len(versions)
        for i, version in enumerate(versions):
            if version in recent_versions:
                colors[i] = recent_colors[recent_versions.index(version)]

        last_slot_data = data[max(x)]
        last_slot_data = {v: last_slot_data[v] for v in recent_versions}
        labels = [f"{v} ({last_slot_data[v]:.2%})" if v in recent_versions else "_nolegend_" for v in versions]
        # add percentage to labels
        ax = plt.subplot(111, frameon=False)
        plt.stackplot(x, *y.values(), labels=labels, colors=colors)
        # hide y axis
        plt.tick_params(axis='y', which='both', left=False, right=False, labelleft=False)
        ax.legend(loc="upper left")
        # add a thin line at current time from y=0 to y=1 with a width of 0.5
        plt.plot([max(x), max(x)], [0, 1], color="white", alpha=0.25)
        # calculate future point to make latest data more visible
        diff = x[-1] - x[0]
        future_point = x[-1] + (diff * 0.05)
        last_y_values = [[yy[-1]] * 2 for yy in y.values()]
        plt.stackplot([x[-1], future_point], *last_y_values, colors=colors)
        plt.tight_layout()

        # the title should mention that the /version_chart command contains more information about how this chart works. but short
        plt.title("READ DESC OF /version_chart IF CONFUSED", y=0.95, fontsize=9)

        # respond with image
        img = BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        e.set_image(url="attachment://chart.png")

        # send data
        await msg.edit(content="", embed=e, attachments=[File(img, filename="chart.png")])
        img.close()

    async def plot_axes_with_data(self, attr: str, ax1, ax2, name, remove_allnodes=False):
        # group by client and get count
        data = await self.gather_attribute(attr, remove_allnodes)

        minipools = [(x, y["validator_count"]) for x, y in data.items() if x != "remove_from_total"]
        minipools = sorted(minipools, key=lambda x: x[1])

        # get total minipool count from rocketpool
        unobserved_minipools = rp.call("rocketMinipoolManager.getStakingMinipoolCount") - sum(d[1] for d in minipools)
        if "remove_from_total" in data:
            unobserved_minipools -= data["remove_from_total"]["validator_count"]
        minipools.insert(0, ("No proposals yet", unobserved_minipools))
        # move "Unknown" to be before "No proposals yet"
        minipools.insert(1, minipools.pop([i for i, (x, y) in enumerate(minipools) if x == "Unknown"][0]))
        # move "External (if it exists) to be before "Unknown"
        # minipools is a list of tuples (name, count)
        if "External" in [x for x, y in minipools]:
            minipools.insert(2, minipools.pop([i for i, (x, y) in enumerate(minipools) if x == "External"][0]))

        # get node operators
        node_operators = [(x, y["count"]) for x, y in data.items() if x != "remove_from_total"]
        node_operators = sorted(node_operators, key=lambda x: x[1])

        # get total node operator count from rp
        unobserved_node_operators = rp.call("rocketNodeManager.getNodeCount") - sum(d[1] for d in node_operators)
        if "remove_from_total" in data:
            unobserved_node_operators -= data["remove_from_total"]["count"]
        node_operators.insert(0, ("No proposals yet", unobserved_node_operators))
        # move "Unknown" to be before "No proposals yet"
        node_operators.insert(1, node_operators.pop([i for i, (x, y) in enumerate(node_operators) if x == "Unknown"][0]))
        # move "External (if it exists) to be before "Unknown"
        # node_operators is a list of tuples (name, count)
        if "External" in [x for x, y in node_operators]:
            node_operators.insert(2, node_operators.pop([i for i, (x, y) in enumerate(node_operators) if x == "External"][0]))

        # sort data
        ax1.pie(
            [x[1] for x in minipools],
            colors=[COLORS.get(x[0], "red") for x in minipools],
            autopct=lambda pct: ('%.1f%%' % pct) if pct > 5 else '',
            startangle=90,
            textprops={'fontsize': '12'},
        )
        # legend
        total_minipols = sum(x[1] for x in minipools)
        # legend in the top left corner of the plot
        ax1.legend(
            [f"{x[1]} {x[0]} ({x[1] / total_minipols:.2%})" for x in minipools],
            loc="lower left",
            bbox_to_anchor=(0, -0.1),
            fontsize=11
        )
        ax1.set_title("Minipools", fontsize=22)

        ax2.pie(
            [x[1] for x in node_operators],
            colors=[COLORS.get(x[0], "#fb5b9d") for x in node_operators],
            autopct=lambda pct: ('%.1f%%' % pct) if pct > 5 else '',
            startangle=90,
            textprops={'fontsize': '12'},
        )
        # legend
        total_node_operators = sum(x[1] for x in node_operators)
        ax2.legend(
            [f"{x[1]} {x[0]} ({x[1] / total_node_operators:.2%})" for x in node_operators],
            loc="lower left",
            bbox_to_anchor=(0, -0.1),
            fontsize=11
        )
        ax2.set_title("Node Operators", fontsize=22)

    async def proposal_vs_node_operators_embed(self, attribute, name, msg, remove_allnodes=False):
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 8))
        # iterate axes in pairs
        title = f"Rocket Pool {name} Distribution {'without Allnodes' if remove_allnodes else ''}"
        await msg.edit(content=f"generating {attribute} distribution graph...")
        await self.plot_axes_with_data(attribute, ax1, ax2, name, remove_allnodes)

        e = Embed(title=title)

        fig.subplots_adjust(left=0, right=1, top=0.9, bottom=0, wspace=0)
        # set title
        fig.suptitle(title, fontsize=24)

        # respond with image
        img = BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        e.set_image(url=f"attachment://{attribute}.png")

        # send data
        f = File(img, filename=f"{attribute}.png")
        img.close()
        return e, f

    @hybrid_command()
    async def client_distribution(self, ctx: Context, remove_allnodes=False):
        """
        Generate a distribution graph of clients.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        embeds, files = [], []
        for attr, name in [["consensus_client", "Consensus Client"], ["execution_client", "Execution Client"]]:
            e, f = await self.proposal_vs_node_operators_embed(attr, name, msg, remove_allnodes)
            embeds.append(e)
            files.append(f)
        await msg.edit(content="", embeds=embeds, attachments=files)

    @hybrid_command()
    async def user_distribution(self, ctx: Context):
        """
        Generate a distribution graph of users.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        e, f = await self.proposal_vs_node_operators_embed("type", "User", msg)
        await msg.edit(content="", embed=e, attachments=[f])

    @hybrid_command()
    async def comments(self, ctx: Context):
        """
        Generate a world cloud of comments.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating comments word cloud...")

        # load image
        mask = np.array(Image.open("./plugins/proposals/assets/logo-words.png"))

        # load font
        font_path = "./plugins/proposals/assets/noto.ttf"

        wc = WordCloud(max_words=2 ** 16,
                       scale=2,
                       mask=mask,
                       max_font_size=100,
                       min_font_size=1,
                       background_color="white",
                       relative_scaling=0,
                       font_path=font_path,
                       color_func=lambda *args, **kwargs: "rgb(235, 142, 85)")

        # aggregate comments with their count
        comments = await self.db.proposals.aggregate([
            {"$match": {"comment": {"$exists": 1}}},
            {"$group": {"_id": "$comment", "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "slot": -1}}
        ]).to_list(None)
        comment_words = {x['_id']: x["count"] for x in comments}

        # generate word cloud
        wc.fit_words(comment_words)

        # respond with image
        img = BytesIO()
        wc.to_image().save(img, format="png")
        img.seek(0)
        plt.close()
        e = Embed(title="Rocket Pool Proposal Comments")
        e.set_image(url="attachment://image.png")
        await msg.edit(content="", embed=e, attachments=[File(img, filename="image.png")])
        img.close()

    @hybrid_command()
    async def client_combo_ranking(self, ctx: Context, remove_allnodes=False, group_by_node_operators=False):
        """
        Generate a ranking of most used execution and consensus clients.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        msg = await self.chore(ctx)
        await msg.edit(content="generating client combo ranking...")

        # aggregate [consensus, execution] pair counts
        client_pairs = await self.db.minipool_proposals.aggregate([
            {
                "$match": {
                    "latest_proposal.consensus_client": {"$ne": "Unknown"},
                    "latest_proposal.execution_client": {"$ne": "Unknown"},
                    "latest_proposal.type"            : {"$ne": "Allnodes"} if remove_allnodes else {"$ne": "deadbeef"}
                }
            }, {
                "$group": {
                    "_id"  : {
                        "consensus": "$latest_proposal.consensus_client",
                        "execution": "$latest_proposal.execution_client"
                    },
                    "count": {
                        "$sum": 1 if group_by_node_operators else "$validator_count"
                    }
                }
            },
            {
                "$sort": {
                    "count": -1
                }
            }
        ]).to_list(None)

        e = Embed(title=f"Client Combo Ranking{' without Allnodes' if remove_allnodes else ''}")

        # generate max width of both columns
        max_widths = [
            max(len(x['_id']['consensus']) for x in client_pairs),
            max(len(x['_id']['execution']) for x in client_pairs),
            max(len(str(x['count'])) for x in client_pairs)
        ]

        desc = "".join(
            f"#{i + 1:<2}\t{pair['_id']['consensus'].rjust(max_widths[0])} & "
            f"{pair['_id']['execution'].ljust(max_widths[1])}\t"
            f"{str(pair['count']).rjust(max_widths[2])}\n"
            for i, pair in enumerate(client_pairs)
        )
        e.description = f"Currently showing {'node operator' if group_by_node_operators else 'validator'} counts\n```{desc}```"
        await msg.edit(content="", embed=e)


async def setup(bot):
    await bot.add_cog(Proposals(bot))
