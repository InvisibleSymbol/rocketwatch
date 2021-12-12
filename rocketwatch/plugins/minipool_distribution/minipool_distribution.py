import logging

import matplotlib.pyplot as plt
import matplotlib.scale as scale
from matplotlib.ticker import ScalarFormatter
from io import BytesIO
import numpy as np

import inflect
from discord import Embed, Color, File, Option
from discord.commands import slash_command
from discord.ext import commands

from utils.cfg import cfg
from utils.slash_permissions import guilds
from utils.thegraph import get_minipool_counts_per_node
from utils.visibility import is_hidden

log = logging.getLogger("RETHAPR")
log.setLevel(cfg["log_level"])
p = inflect.engine()

def get_percentiles(percentiles, counts):
    for p in percentiles:
        yield (p, np.percentile(counts, p, interpolation='nearest'))

class MinipoolDistribution(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    async def minipool_distribution_raw(self, ctx, distribution):
        e = Embed(color=self.color)
        e.title = "Minipool Distribution"
        description = "```\n"
        for minipools, nodes in distribution:
            description += f"{p.no('minipool', minipools):>14}: " \
                           f"{nodes:>4} {p.plural('node', nodes)}\n"
        description += "```"
        e.description = description
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))

    @slash_command(guild_ids=guilds)
    async def minipool_distribution(self,
                                    ctx,
                                    raw: Option(
                                        bool,
                                        "Show Raw Distribution Data",
                                        default=False,
                                        required=False)):
        await ctx.defer(ephemeral=is_hidden(ctx))
        # Get the minipool distribution
        counts = get_minipool_counts_per_node()
        # Converts the array of counts, eg [ 0, 0, 0, 1, 1, 2 ], to a list of tuples
        # where the first item is the number of minipools and the second item is the
        # number of nodes, eg [ (0, 3), (1, 2), (2, 1) ]
        bins = np.bincount(counts)
        distribution = [ (i, bins[i]) for i in range(0, len(bins)) if bins[i] > 0 ]

        # If the raw data were requested, print them and exit early
        if raw:
            await self.minipool_distribution_raw(ctx, distribution[::-1])
            return

        img = BytesIO()
        fig, (ax, ax2) = plt.subplots(2, 1)

        # First chart is sorted bars showing total minipools provided by nodes with x minipools per node
        bars = { x: x*y for x, y in distribution }
        # Remove the 0,0 value, since it doesn't provide any insight
        del bars[0]
        x_keys = [str(x) for x in bars.keys()]
        rects = ax.bar(x_keys, bars.values(), color=str(self.color))
        ax.bar_label(rects)
        ax.set_ylabel("Total Minipools")
        # Offset every other x tick, so the numbers don't bunch up
        for label in ax.xaxis.get_major_ticks()[1::2]:
            label.set_pad(10)
        # Add a 5% buffer to the ylim to help fit all the bar labels
        ax.set_ylim(top=(ax.get_ylim()[1] * 1.05))

        # Second chart is a line graph showing the total number of nodes with x minipools per node, logscale
        ax2.plot([x[0] for x in distribution], [x[1] for x in distribution], color=str(self.color))
        ax2.set_xscale(scale.SymmetricalLogScale(ax2, base=10, linthresh=10))
        ax2.set_yscale(scale.SymmetricalLogScale(ax2, base=10, linthresh=1))
        ax2.xaxis.set_major_formatter(ScalarFormatter())
        ax2.yaxis.set_major_formatter(ScalarFormatter())
        ax2.set_xlabel("Minipools per Node")
        ax2.set_ylabel("Total Nodes")

        fig.tight_layout()
        fig.savefig(img, format='png')
        img.seek(0)

        fig.clf()
        plt.close()

        e = Embed(color=self.color)
        e.title = "Minipool Distribution"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        percentile_strings = [f"{x[0]}th percentile: {p.no('minipool', x[1])} per node" for x in get_percentiles([50, 75, 90, 99], counts)]
        percentile_strings.append(f"Max: {distribution[-1][0]} minipools per node")
        percentile_strings.append(f"Total: {p.no('minipool', sum(counts))}")
        e.set_footer(text= "\n".join(percentile_strings))
        await ctx.respond(embed=e, file=f, ephemeral=is_hidden(ctx))
        img.close()


def setup(bot):
    bot.add_cog(MinipoolDistribution(bot))
