from datetime import datetime

import pytz
from discord import Embed, Color
from discord.ext import commands
from discord_slash import cog_ext

from utils import solidity
from utils.rocketpool import rp
from utils.slash_permissions import guilds


class Random(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @cog_ext.cog_slash(guild_ids=guilds)
    async def deposit_pool(self, ctx):
        e = Embed(colour=self.color)
        deposit_pool = solidity.to_float(rp.call("rocketDepositPool.getBalance"))
        deposit_cap = solidity.to_float(rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositPoolSize"))
        e.title = "Deposit Pool Stats"
        e.add_field(name="Current Size", value=f"{deposit_pool:.3f} ETH")
        e.add_field(name="Maximum Size", value=f"{deposit_cap:.3f} ETH")
        e.add_field(name="Percentage Full", value=f"{deposit_pool / deposit_cap * 100:.2f}%", inline=False)
        await ctx.send(embed=e)

    @cog_ext.cog_slash(guild_ids=guilds)
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

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Random(bot))
