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
    async def dp(self, ctx):
        """Deposit Pool Stats"""
        await self._dp(ctx)

    @cog_ext.cog_slash(guild_ids=guilds)
    async def deposit_pool(self, ctx):
        """Deposit Pool Stats"""
        await self._dp(ctx)

    async def _dp(self, ctx):
        await ctx.defer()
        e = Embed(colour=self.color)
        e.title = "Deposit Pool Stats"

        deposit_pool = solidity.to_float(rp.call("rocketDepositPool.getBalance"))
        e.add_field(name="Current Size:", value=f"{humanize.intcomma(round(deposit_pool, 3))} ETH")

        deposit_cap = solidity.to_int(rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositPoolSize"))
        e.add_field(name="Maximum Size:", value=f"{humanize.intcomma(deposit_cap)} ETH")

        if deposit_cap - deposit_pool < 0.01:
            e.add_field(name="Status:",
                        value=f"Deposit Pool Cap Reached!",
                        inline=False)
        else:
            percentage_filled = round(deposit_pool / deposit_cap * 100, 2)
            free_capacity = round(deposit_cap - deposit_pool, 3)
            e.add_field(name="Status:",
                        value=f"{percentage_filled}% Full. Enough space for {humanize.intcomma(free_capacity)} more ETH",
                        inline=False)

        minipool_count = int(deposit_pool / 16)
        e.add_field(name="Enough For:", value=f"{minipool_count} new Minipools")

        current_commission = round(solidity.to_float(rp.call("rocketNetworkFees.getNodeFee")) * 100, 2)
        e.add_field(name="Current Commission Rate:", value=f"{current_commission}%")

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

    @cog_ext.cog_slash(guild_ids=guilds)
    async def rewards_explained(self, ctx):
        """Confused about your first Rewards? So am I! Hope this command can help though lol"""
        embed = Embed(color=self.color)
        with open("plugins/random/rewards_explained.txt", "r") as f:
            embed.title = "#trading Rewards Explanation"
            embed.description = f.read()
        embed.set_footer(text="Noticed anything wrong/confusing? Mention @Invis or @knoshua")

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Random(bot))
