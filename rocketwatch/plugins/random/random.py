from datetime import datetime

import humanize
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
        deposit_pool = round(solidity.to_float(rp.call("rocketDepositPool.getBalance")),3)
        deposit_cap = solidity.to_int(rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositPoolSize"))
        deposit_free_capacity = deposit_cap - deposit_pool
        current_commission = solidity.to_float(rp.call("rocketNetworkFees.getNodeFee"))
        e.title = "Deposit Pool Stats"
        e.add_field(name="Current Size", value=f"{humanize.intcomma(deposit_pool)} ETH")
        e.add_field(name="Maximum Size", value=f"{humanize.intcomma(deposit_cap)} ETH")
        e.add_field(name="Free Capacity", value=f"{humanize.intcomma(deposit_free_capacity)} ETH")
        e.add_field(name="Percentage Full", value=f"{deposit_pool / deposit_cap * 100:.2f}%", inline=False)
        e.add_field(name="Enough For:", value=f"{int(deposit_pool/16)} new Minipools")
        e.add_field(name="Current Commission", value=f"{current_commission * 100:.2f}%")
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
