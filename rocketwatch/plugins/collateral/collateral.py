import logging
from io import BytesIO

import inflect
import matplotlib.pyplot as plt
import numpy as np
from discord import File
from discord.app_commands import describe
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command
from matplotlib.ticker import FuncFormatter

from utils.cfg import cfg
from utils.embeds import Embed
from utils.thegraph import get_average_collateral_percentage_per_node
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
    @describe(raw="Show Raw Distribution Data",
              cap_collateral="Cap Collateral to 150%")
    async def collateral_distribution(self,
                                      ctx: Context,
                                      raw: bool = False,
                                      cap_collateral: bool = True,
                                      collateral_cap: int = 150):
        """
        Show the distribution of collateral across nodes.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()

        data = get_average_collateral_percentage_per_node(collateral_cap or 150 if cap_collateral else None)

        counts = []
        for collateral, nodes in data.items():
            counts.extend([collateral] * len(nodes))
        counts = list(sorted(counts))
        bins = np.bincount(counts)
        distribution = [(i, bins[i]) for i in range(len(bins)) if bins[i] > 0]

        # If the raw data were requested, print them and exit early
        if raw:
            await collateral_distribution_raw(ctx, distribution[::-1])
            return

        img = BytesIO()
        # create figure with 2 separate y axes
        fig, ax = plt.subplots()
        ax2 = ax.twinx()

        bars = dict(distribution)
        if 0 in bars:
            del bars[0]
        x_keys = [str(x) for x in bars]
        rects = ax.bar(x_keys, bars.values(), color=str(e.color))
        ax.bar_label(rects)

        ax.set_xticklabels(x_keys, rotation=45, ha="right")

        for label in ax.xaxis.get_major_ticks()[1::2]:
            label.label.set_visible(False)
        ax.set_ylim(top=(ax.get_ylim()[1] * 1.1))
        ax.yaxis.set_visible(False)
        ax.get_xaxis().set_major_formatter(FuncFormatter(lambda n, _: f"{x_keys[n] if n < len(x_keys) else 0}%"))

        staked_distribution = [
            (collateral, sum(nodes)) for collateral, nodes in sorted(data.items(), key=lambda x: x[0])
        ]

        bars = dict(staked_distribution)
        if 0 in bars:
            del bars[0]
        line = ax2.plot(x_keys, bars.values())
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
