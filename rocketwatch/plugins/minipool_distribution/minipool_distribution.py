import logging
from io import BytesIO

import inflect
import matplotlib.pyplot as plt
import numpy as np
import pymongo
from discord import File
from discord.app_commands import describe
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command

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
        # 2 nodes have 0 minipools
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
        percentile_strings = [f"{x[0]}th percentile: {p.no('minipool', int(x[1]))} per node" for x in
                              get_percentiles([50, 75, 90, 99], counts) if x[1]]
        percentile_strings.append(f"Max: {distribution[-1][0]} minipools per node")
        percentile_strings.append(f"Total: {p.no('minipool', sum(counts))}")
        e.set_footer(text="\n".join(percentile_strings))
        await ctx.send(embed=e, files=[f])
        img.close()

    @hybrid_command()
    @describe(raw="Show the raw distribution data")
    async def node_gini(self, ctx: Context, raw: bool = False):
        """
        Show the cumulative validator share of the largest nodes.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        e.title = "Validator Share of Largest Nodes"

        minipool_counts = np.array(self.get_minipool_counts_per_node())
        # sort descending
        minipool_counts[::-1].sort()

        # divide by sum to get protocol share
        y = minipool_counts.cumsum() / minipool_counts.sum()
        x = np.arange(1, len(y) + 1)

        # calculate gini coefficient from sorted list
        counts_nz = minipool_counts[minipool_counts != 0]
        n_nz = counts_nz.size
        gini = -(((2 * np.arange(1, n_nz + 1) - n_nz - 1) * counts_nz).sum() / (n_nz * counts_nz.sum()))

        e.set_footer(text=f"Gini coefficient: {gini:.4f}")

        if raw:
            description = ""
            # count number of nodes in 5% intervals + significant thresholds
            ticks = list(np.arange(0.05, 1, 0.05)) + [1 / 3, 2 / 3, 1.0]
            for threshold in sorted(ticks):
                index = y.searchsorted(threshold)
                num_nodes = x[index]
                node_txt = "node" if num_nodes == 1 else "nodes"
                description += f"{round(100 * threshold)}%: {num_nodes} {node_txt}\n"

            description += f"\nTotal: {x[-1]} nodes"
            e.description = description
            await ctx.send(embed=e)
            return

        fig, ax = plt.subplots(1, 1)

        ax.plot(x, y)
        ax.set_xlabel("number of nodes")
        ax.set_ylabel("protocol share")
        ax.set_xscale("log")
        ax.set_xlim((1, x[-1]))
        ax.set_ylim((0, 1))

        x_ticks = [x[-1]]

        def draw_threshold(threshold: float, color: str) -> None:
            index = y.searchsorted(threshold)
            x_pos = x[index]
            percentage = round(100 * threshold)
            x_ticks.append(x_pos)
            ax.axvline(x=x_pos, linestyle='--', c=color, label=f'{percentage}%')

        draw_threshold(1 / 3, "tab:green")
        draw_threshold(0.5, "tab:olive")
        draw_threshold(2 / 3, "tab:orange")
        draw_threshold(0.9, "tab:red")

        # add powers of 10 to x ticks if not too close to existing ticks
        i = 1
        while i < x[-1]:
            if not any((i / 1.5 < tick < i * 1.5) for tick in x_ticks):
                x_ticks.append(i)
            i *= 10

        ax.set_xticks(x_ticks, map(str, x_ticks))
        ax.legend()

        fig.tight_layout()

        img = BytesIO()
        fig.savefig(img, format="png")
        img.seek(0)

        fig.clf()
        plt.close()

        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")

        await ctx.send(embed=e, files=[f])
        img.close()


async def setup(bot):
    await bot.add_cog(MinipoolDistribution(bot))
