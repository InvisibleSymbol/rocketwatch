import logging
from io import BytesIO

import inflect
import matplotlib.pyplot as plt
import numpy as np
from discord import Embed, Color, File, Option
from discord.commands import slash_command
from discord.ext import commands
from matplotlib.ticker import FuncFormatter

from utils.cfg import cfg
from utils.slash_permissions import guilds
from utils.thegraph import get_average_collateral_percentage_per_node
from utils.visibility import is_hidden

log = logging.getLogger("collateral")
log.setLevel(cfg["log_level"])
p = inflect.engine()


def get_percentiles(percentiles, counts):
    for p in percentiles:
        yield (p, np.percentile(counts, p, interpolation='nearest'))


class Collateral(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    async def collateral_distribution_raw(self, ctx, distribution):
        e = Embed(color=self.color)
        e.title = "Collateral Distribution"
        description = "```\n"
        for collateral, nodes in distribution:
            description += f"{collateral:>5}%: " \
                           f"{nodes:>4} {p.plural('node', nodes)}\n"
        description += "```"
        e.description = description
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))

    @slash_command(guild_ids=guilds)
    async def collateral_distribution(self,
                                      ctx,
                                      raw: Option(
                                          bool,
                                          "Show Raw Distribution Data",
                                          default=False,
                                          required=False),
                                      cap_collateral: Option(
                                          bool,
                                          "Cap Collateral to 150%",
                                          default=True,
                                          required=False)):
        await ctx.defer(ephemeral=is_hidden(ctx))
        data = get_average_collateral_percentage_per_node(cap_collateral)
        counts = []
        for collateral, nodes in data.items():
            counts.extend([collateral] * len(nodes))
        counts = list(sorted(counts))
        bins = np.bincount(counts)
        distribution = [(i, bins[i]) for i in range(len(bins)) if bins[i] > 0]

        # If the raw data were requested, print them and exit early
        if raw:
            await self.collateral_distribution_raw(ctx, distribution[::-1])
            return

        img = BytesIO()
        # create figure with 2 separate y axes
        fig, ax = plt.subplots()
        ax2 = ax.twinx()

        bars = {x: y for x, y in distribution}
        if 0 in bars:
            del bars[0]
        x_keys = [str(x) for x in bars]
        rects = ax.bar(x_keys, bars.values(), color=str(self.color))
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

        bars = {x: y for x, y in staked_distribution}
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

        e = Embed(color=self.color)
        e.title = "Average Collateral Distribution"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        percentile_strings = [f"{x[0]}th percentile: {int(x[1])}% collateral" for x in
                              get_percentiles([50, 75, 90, 99], counts)]
        e.set_footer(text="\n".join(percentile_strings))
        await ctx.respond(embed=e, file=f, ephemeral=is_hidden(ctx))
        img.close()


def setup(bot):
    bot.add_cog(Collateral(bot))
