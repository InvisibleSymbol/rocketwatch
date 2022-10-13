import logging
from io import BytesIO

import inflect
import matplotlib.pyplot as plt
import matplotlib.scale as scale
import numpy as np
import pymongo
from discord import File
from discord.app_commands import describe
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command
from matplotlib.ticker import ScalarFormatter

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden

log = logging.getLogger("minipool_distribution")
log.setLevel(cfg["log_level"])
p = inflect.engine()


def get_percentiles(percentiles, counts):
    for p in percentiles:
        yield p, np.percentile(counts, p, interpolation='nearest')


async def minipool_distribution_raw(ctx: Context, distribution):
    e = Embed()
    e.title = "Minipool Distribution"
    description = "```\n"
    for minipools, nodes in distribution:
        description += f"{p.no('minipool', minipools):>14}: " \
                       f"{nodes:>4} {p.plural('node', nodes)}\n"
    description += "```"
    e.description = description
    await ctx.send(embed=e)


class MinipoolDistribution(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.mongo = pymongo.MongoClient(cfg["mongodb_uri"])
        self.db = self.mongo.rocketwatch

    def get_minipool_counts_per_node(self):
        # get an array for minipool counts per node from db using aggregation
        # example: [0,0,1,2,3,3,3]
        # 0 nodes have 0 minipools
        # 1 node has 1 minipool
        # 1 node has 2 minipools
        # 3 nodes have 3 minipools
        pipeline = [
            {"$group": {"_id": "$node_operator", "count": {"$sum": 1}}},
            {"$sort": {"count": 1}}
        ]
        return [x["count"] for x in self.db.minipools.aggregate(pipeline)]

    @hybrid_command()
    @describe(raw="Show the raw Distribution Data")
    async def minipool_distribution(self,
                                    ctx: Context,
                                    raw: bool = False):
        """
        Show the distribution of minipools per node.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()

        # Get the minipool distribution
        counts = self.get_minipool_counts_per_node()
        # Converts the array of counts, eg [ 0, 0, 0, 1, 1, 2 ], to a list of tuples
        # where the first item is the number of minipools and the second item is the
        # number of nodes, eg [ (0, 3), (1, 2), (2, 1) ]
        bins = np.bincount(counts)
        distribution = [(i, bins[i]) for i in range(len(bins)) if bins[i] > 0]

        # If the raw data were requested, print them and exit early
        if raw:
            await minipool_distribution_raw(ctx, distribution[::-1])
            return

        img = BytesIO()
        fig, ax = plt.subplots(1, 1)

        # First chart is sorted bars showing total minipools provided by nodes with x minipools per node
        bars = {x: x * y for x, y in distribution}
        # Remove the 0,0 value, since it doesn't provide any insight
        x_keys = [str(x) for x in bars]
        rects = ax.bar(x_keys, bars.values(), color=str(e.color))
        ax.bar_label(rects, rotation=90, padding=3, fontsize=7)
        ax.set_ylabel("Total Minipools")
        # tilt the x axis labels
        ax.tick_params(axis='x', labelrotation=90, labelsize=7)
        # Add a 5% buffer to the ylim to help fit all the bar labels
        ax.set_ylim(top=(ax.get_ylim()[1] * 1.1))

        fig.tight_layout()
        fig.savefig(img, format='png')
        img.seek(0)

        fig.clf()
        plt.close()

        e.title = "Minipool Distribution"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        percentile_strings = [f"{x[0]}th percentile: {p.no('minipool', x[1])} per node" for x in
                              get_percentiles([50, 75, 90, 99], counts)]
        percentile_strings.append(f"Max: {distribution[-1][0]} minipools per node")
        percentile_strings.append(f"Total: {p.no('minipool', sum(counts))}")
        e.set_footer(text="\n".join(percentile_strings))
        await ctx.send(embed=e, files=[f])
        img.close()



async def setup(bot):
    await bot.add_cog(MinipoolDistribution(bot))
