import logging
from io import BytesIO

import inflect
import math
import matplotlib.pyplot as plt
import numpy as np
from discord import File
from discord.app_commands import describe
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command
from ens import InvalidName
from matplotlib.ticker import FuncFormatter

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, ens
from utils.rocketpool import rp
from utils.shared_w3 import w3
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

        address = None
        if node_address is not None:
            if "." in node_address:
                try:
                    address = ens.resolve_name(node_address)
                    if not address:
                        await ctx.send("ENS name not found")
                        return
                except InvalidName:
                    await ctx.send("Invalid ENS name")
                    return
            else:
                try:
                    address = w3.toChecksumAddress(node_address)
                except InvalidName:
                    await ctx.send("Invalid address")
                    return

        rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        data = get_node_minipools_and_collateral()

        # Calculate each node's tvl and collateral and add it to the data
        def node_tvl(node):
            return int(node["eb8s"]) * 8 + int(node["eb16s"]) * 16 + solidity.to_float(node["rplStaked"]) * rpl_price

        def node_collateral(node):
            eth = int(node["eb16s"]) * 16 + int(node["eb8s"]) * (8 if bonded else 24)
            if not eth:
                return 0
            return 100 * (solidity.to_float(node["rplStaked"]) * rpl_price) / eth

        def minipools(node):
            return int(node["eb16s"]) + int(node["eb8s"])

        for node in data.values():
            node.update(
                {
                    "tvl": node_tvl(node),
                    "collateral": node_collateral(node),
                    "minipools": minipools(node)
                }
            )

        nodes = list(filter(lambda node: node["minipools"] != 0, data.values()))

        # sort nodes ascending by tvl
        nodes.sort(key=lambda node: node["tvl"])

        # create the scatter plot
        x = [node["tvl"] for node in nodes]
        y = [node["collateral"] for node in nodes]
        c = [math.log10(node["minipools"]) for node in nodes]
        max_minipools = max([node["minipools"] for node in nodes])

        e = Embed()
        img = BytesIO()
        fig, ax = plt.subplots()
        ax.set_xscale("log")
        paths = ax.scatter(x, y, c=c, alpha=0.33)
        legend = ax.legend(*paths.legend_elements(func=lambda x: 10**x, num=[1,10,100,max_minipools]), loc="upper right", title="Minipools")
        ax.add_artist(legend)
        ax.set_ylabel(f"Collateral (percent {'bonded' if bonded else 'borrowed'})")
        ax.yaxis.set_major_formatter("{x:.0f}%")
        ax.set_xlabel("Node TVL in Eth")
        ax.xaxis.set_major_formatter(lambda x, _: "{:.1f}k".format(x / 1000))

        # Add lines
        if node_address is not None:
            # Print a vline and hline through the requested node
            target_node = data[address]
            if target_node:
                plt.plot(node_tvl(target_node), node_collateral(target_node), 'ro')
                e.set_footer_parts([f"Showing location of {node_address}"])
            else:
                e.set_footer_parts([f"{node_address} not found in set"])
        if not bonded:
            ax.axhline(y=10)
            ax.axhline(y=15)

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
