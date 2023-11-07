import logging
from io import BytesIO

import inflect
import math
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from discord import File
from discord.app_commands import describe
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command
from matplotlib.ticker import FuncFormatter

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, resolve_ens
from utils.rocketpool import rp
from utils.thegraph import get_average_collateral_percentage_per_node, get_node_minipools_and_collateral
from utils.visibility import is_hidden

log = logging.getLogger("collateral")
log.setLevel(cfg["log_level"])
p = inflect.engine()


def get_percentiles(percentiles, counts):
    for p in percentiles:
        yield p, np.percentile(counts, p, interpolation='nearest')


async def collateral_distribution_raw(ctx: Context, distribution):
    e = Embed()
    e.title = "Collateral Distribution"
    description = "```\n"
    for collateral, nodes in distribution:
        description += f"{collateral:>5}%: " \
                       f"{nodes:>4} {p.plural('node', nodes)}\n"
    description += "```"
    e.description = description
    await ctx.send(embed=e)


class Collateral(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    @describe(node_address="Node Address or ENS to highlight",
              bonded="Calculate collateral as a percent of bonded eth instead of borrowed")
    async def node_tvl_vs_collateral(self,
                                     ctx: Context,
                                     node_address: str = None,
                                     bonded: bool = False):
        """
        Show a scatter plot of collateral ratios for given node TVLs
        """
        await ctx.defer(ephemeral=is_hidden(ctx))

        display_name = None
        address = None
        if node_address is not None:
            display_name, address = await resolve_ens(ctx, node_address)
            if display_name is None:
                return

        rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        data = get_node_minipools_and_collateral()

        # Calculate each node's tvl and collateral and add it to the data
        def node_tvl(node):
            return int(node["eb8s"]) * 8 + int(node["eb16s"]) * 16

        def node_collateral(node):
            eth = int(node["eb16s"]) * 16 + int(node["eb8s"]) * (8 if bonded else 24)
            if not eth:
                return 0
            return 100 * (solidity.to_float(node["rplStaked"]) * rpl_price) / eth

        def node_minipools(node):
            return int(node["eb16s"]) + int(node["eb8s"])

        x, y, c = [], [], []
        max_minipools = 0
        for node in data.values():
            minis = node_minipools(node)
            if minis <= 0:
                continue

            x.append(node_tvl(node))
            y.append(node_collateral(node))
            c.append(minis)
            max_minipools = max(max_minipools, minis)

        e = Embed()
        img = BytesIO()
        fig, (ax, ax2) = plt.subplots(2)
        fig.set_figheight(fig.get_figheight() * 2)

        # create the scatter plot
        paths = ax.scatter(x, y, c=c, alpha=0.25, norm="log")
        polys = ax2.hexbin(x, y, gridsize=20, bins="log", xscale="log", cmap="viridis")
        # fill the background in with the default color.
        ax2.set_facecolor(mpl.colors.to_rgba(mpl.colormaps["viridis"](0), 0.9))
        max_nodes = max(polys.get_array())

        # log-scale the X-axis to account for thomas
        ax.set_xscale("log", base=8)

        # Add a legend for the color-coding on the scatter plot
        formatToInt = "{x:.0f}"
        cb = plt.colorbar(mappable=paths, ax=ax, format=formatToInt)
        cb.set_label('Minipools')
        cb.set_ticks([1,10,100,max_minipools])

        # Add a legend for the color-coding on the hex distribution
        cb = plt.colorbar(mappable=polys, ax=ax2, format=formatToInt)
        cb.set_label('Nodes')
        cb.set_ticks([1,10,100,max_nodes - 1])

        # Add labels and units
        ylabel = f"Collateral (percent {'bonded' if bonded else 'borrowed'})"
        ax.set_ylabel(ylabel)
        ax2.set_ylabel(ylabel)
        ax.yaxis.set_major_formatter(formatToInt + "%")
        ax2.yaxis.set_major_formatter(formatToInt + "%")
        ax2.set_xlabel("Node Bond (Eth only - log scale)")
        ax.xaxis.set_major_formatter(formatToInt)
        ax2.xaxis.set_major_formatter(formatToInt)

        # Add a red dot if the user asked to highlight their node
        if address is not None:
            # Print a vline and hline through the requested node
            try:
                target_node = data[address]
                ax.plot(node_tvl(target_node), node_collateral(target_node), 'ro')
                ax2.plot(node_tvl(target_node), node_collateral(target_node), 'ro')
                e.description = f"Showing location of {display_name}"
            except KeyError:
                await ctx.send(f"{display_name} not found in data set - it must have at least one minipool")
                return

        # Add horizontal lines showing the 10-15% range made optimal by RPIP-30
        if not bonded:
            ax.axhspan(10, 15, alpha=0.1, color="grey")

        fig.tight_layout()

        img = BytesIO()
        fig.savefig(img, format='png')
        img.seek(0)
        fig.clf()
        plt.close()

        e.title = "Node TVL vs Collateral Scatter Plot"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        await ctx.send(embed=e, files=[f])
        img.close()

    @hybrid_command()
    @describe(raw="Show Raw Distribution Data",
              cap_collateral="Cap Collateral to 150%",
              bonded="Calculate collateral as percent of bonded eth instead of borrowed")
    async def collateral_distribution(self,
                                      ctx: Context,
                                      raw: bool = False,
                                      cap_collateral: bool = True,
                                      collateral_cap: int = 150,
                                      bonded: bool = False):
        """
        Show the distribution of collateral across nodes.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))

        data = get_average_collateral_percentage_per_node(collateral_cap or 150 if cap_collateral else None, bonded)

        counts = []
        for collateral, nodes in data.items():
            counts.extend([collateral] * len(nodes))
        counts = list(sorted(counts))
        bins = np.bincount(counts)
        distribution = [(i, bins[i]) for i in range(len(bins)) if i % 5 == 0]

        # If the raw data were requested, print them and exit early
        if raw:
            await collateral_distribution_raw(ctx, distribution[::-1])
            return

        e = Embed()
        img = BytesIO()
        # create figure with 2 separate y axes
        fig, ax = plt.subplots()
        ax2 = ax.twinx()

        bars = dict(distribution)
        x_keys = [str(x) for x in bars]
        rects = ax.bar(x_keys, bars.values(), color=str(e.color), align='edge')
        ax.bar_label(rects)

        ax.set_xticklabels(x_keys, rotation='vertical')
        ax.set_xlabel(f"Collateral Percent of { 'Bonded' if bonded else 'Borrowed'} Eth")

        for label in ax.xaxis.get_major_ticks()[1::2]:
            label.label.set_visible(False)
        ax.set_ylim(top=(ax.get_ylim()[1] * 1.1))
        ax.yaxis.set_visible(False)
        ax.get_xaxis().set_major_formatter(FuncFormatter(
            lambda n, _: f"{x_keys[n] if n < len(x_keys) else 0}{'+' if n == len(x_keys)-1 and cap_collateral else ''}%")
        )

        staked_distribution = [
            (collateral, sum(nodes)) for collateral, nodes in sorted(data.items(), key=lambda x: x[0])
        ]

        bars = dict(staked_distribution)
        line = ax2.plot(x_keys, [bars.get(int(x), 0) for x in x_keys])
        ax2.set_ylim(top=(ax2.get_ylim()[1] * 1.1))
        ax2.tick_params(axis='y', colors=line[0].get_color())
        ax2.get_yaxis().set_major_formatter(FuncFormatter(lambda y, _: f"{int(y / 10 ** 3)}k"))

        fig.tight_layout()
        ax.legend(rects, ["Node Operators"], loc="upper left")
        ax2.legend(line, ["Effective Staked RPL"], loc="upper right")
        fig.savefig(img, format='png')
        img.seek(0)

        fig.clf()
        plt.close()

        e.title = "Average Collateral Distribution"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        percentile_strings = [f"{x[0]}th percentile: {int(x[1])}% collateral" for x in
                              get_percentiles([50, 75, 90, 99], counts)]
        e.description = f"Total Effective Staked RPL: {sum(bars.values()):,}"
        e.set_footer(text="\n".join(percentile_strings))
        await ctx.send(embed=e, files=[f])
        img.close()


async def setup(bot):
    await bot.add_cog(Collateral(bot))
