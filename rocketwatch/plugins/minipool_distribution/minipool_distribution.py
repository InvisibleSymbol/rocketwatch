import logging

import inflect
from discord import Embed, Color
from discord.commands import slash_command
from discord.ext import commands

from utils.cfg import cfg
from utils.slash_permissions import guilds
from utils.thegraph import get_minipool_count_per_node_histogram
from utils.visibility import is_hidden

log = logging.getLogger("RETHAPR")
log.setLevel(cfg["log_level"])
p = inflect.engine()


class MinipoolDistribution(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def minipool_distribution(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        # Get the minipool distribution
        minipool_distribution = get_minipool_count_per_node_histogram()

        e = Embed(color=self.color)
        e.title = "Minipool Distribution"
        description = "```\n"
        for minipools, nodes in minipool_distribution:
            description += f"{p.no('minipool', minipools):>14}: " \
                           f"{nodes:>4} {p.plural('node', nodes)}\n"
        description += "```"
        e.description = description
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(MinipoolDistribution(bot))
