import logging
from datetime import datetime

import pytz
from discord import Embed, Color
from discord.commands import slash_command
from discord.ext import commands

from utils.cfg import cfg
from utils.sea_creatures import sea_creatures
from utils.slash_permissions import guilds

log = logging.getLogger("random")
log.setLevel(cfg["log_level"])


class Random(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def dev_time(self, ctx):
        """Timezones too confusing to you? Well worry no more, this command is here to help!"""
        embed = Embed(color=self.color)
        time_format = "%A %H:%M:%S %Z"

        dev_time = datetime.now(tz=pytz.timezone("UTC"))
        embed.add_field(name="Coordinated Universal Time", value=dev_time.strftime(time_format), inline=False)

        dev_time = datetime.now(tz=pytz.timezone("Australia/Lindeman"))
        embed.add_field(name="Time for most of the Dev Team", value=dev_time.strftime(time_format), inline=False)

        joe_time = datetime.now(tz=pytz.timezone("America/New_York"))
        embed.add_field(name="Joe's Time", value=joe_time.strftime(time_format), inline=False)

        await ctx.respond(embed=embed)

    @slash_command(guild_ids=guilds)
    async def sea_creatures(self, ctx):
        """List all sea creatures with their required minimum holding"""
        embed = Embed(color=self.color)
        embed.title = "Possible Sea Creatures"
        embed.description = "RPL (both old and new), rETH and ETH are consider as assets for the sea creature determination!"
        for holding_value, sea_creature in sea_creatures.items():
            embed.add_field(name=sea_creature, value=f"holds over {holding_value} ETH worth of assets", inline=False)

        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(Random(bot))
