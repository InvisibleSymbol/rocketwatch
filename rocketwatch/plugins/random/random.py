from datetime import datetime
from io import BytesIO

import humanize
import pytz
from discord import Embed, Color
from discord import File
from discord.ext import commands
from discord_slash import cog_ext

from utils import solidity
from utils.deposit_pool_graph import get_graph
from utils.readable import etherscan_url
from utils.rocketpool import rp
from utils.slash_permissions import guilds
from utils.visibility import is_hidden


class Random(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @cog_ext.cog_slash(guild_ids=guilds)
    async def queue(self, ctx):
        """Show the next 10 minipools in the queue"""
        await ctx.defer(hidden=is_hidden(ctx))
        e = Embed(colour=self.color)
        e.title = "Minipool queue"

        # Get the next 10 minipools per category
        minipools = rp.get_minipools(limit=10)
        description = ""
        if minipools["half"]:
            data = minipools["half"]
            description += f"**Normal Minipool Queue:** ({data[0]} Minipools)\n- "
            description += "\n- ".join([etherscan_url(m, f'`{m}`') for m in data[1]])
            description += "\n- ...\n\n"
        if minipools["full"]:
            data = minipools["full"]
            description += f"**32 ETH Minipool Refund Queue:** ({data[0]} Minipools)\n- "
            description += "\n- ".join([etherscan_url(m, f'`{m}`') for m in data[1]])
            description += "\n- ...\n\n"
        if minipools["empty"]:
            data = minipools["empty"]
            description += f"**Unbonded Minipool**: ({data[0]} Minipools)\n- "
            description += "\n- ".join([etherscan_url(m, f'`{m}`') for m in data[1]])
            description += "\n- ...\n\n"
        e.description = description
        await ctx.send(embed=e, hidden=is_hidden(ctx))

    @cog_ext.cog_slash(guild_ids=guilds)
    async def dp(self, ctx):
        """Deposit Pool Stats"""
        await self._dp(ctx)

    @cog_ext.cog_slash(guild_ids=guilds)
    async def deposit_pool(self, ctx):
        """Deposit Pool Stats"""
        await self._dp(ctx)

    async def _dp(self, ctx):
        await ctx.defer(hidden=is_hidden(ctx))
        e = Embed(colour=self.color)
        e.title = "Deposit Pool Stats"

        deposit_pool = solidity.to_float(rp.call("rocketDepositPool.getBalance"))
        e.add_field(name="Current Size:", value=f"{humanize.intcomma(round(deposit_pool, 3))} ETH")

        deposit_cap = solidity.to_int(rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositPoolSize"))
        e.add_field(name="Maximum Size:", value=f"{humanize.intcomma(deposit_cap)} ETH")

        current_node_demand = solidity.to_float(rp.call("rocketNetworkFees.getNodeDemand"))

        if deposit_cap - deposit_pool < 0.01:
            e.add_field(name="Status:",
                        value=f"Deposit Pool Cap Reached!",
                        inline=False)
        else:
            percentage_filled = round(deposit_pool / deposit_cap * 100, 2)
            free_capacity = deposit_cap - deposit_pool
            if current_node_demand <= 0:
                free_capacity += current_node_demand * -1
            free_capacity = round(free_capacity, 3)
            e.add_field(name="Status:",
                        value=f"{percentage_filled}% Full. Enough space for {humanize.intcomma(free_capacity)} more ETH",
                        inline=False)

        current_commission = solidity.to_float(rp.call("rocketNetworkFees.getNodeFee")) * 100
        e.add_field(name="Current Commission Rate:", value=f"{round(current_commission, 2)}%", inline=False)

        minipool_count = int(deposit_pool / 16)
        e.add_field(name="Enough For:", value=f"{minipool_count} new Minipools")

        queue_length = rp.call("rocketMinipoolQueue.getTotalLength")
        e.add_field(name="Current Queue:", value=f"{humanize.intcomma(queue_length)} Minipools")

        img = BytesIO()
        rendered_graph = get_graph(img, current_commission, current_node_demand)
        if rendered_graph:
            e.set_image(url="attachment://graph.png")
            f = File(img, filename="graph.png")
            await ctx.send(embed=e, file=f, hidden=is_hidden(ctx))
        else:
            await ctx.send(embed=e, hidden=is_hidden(ctx))
        img.close()

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
    async def tvl(self, ctx):
        tvl = []

        description = []
        tvl.append(solidity.to_float(rp.call("rocketTokenRETH.getTotalCollateral")))
        description.append(f"  {tvl[-1]:12.2f} ETH: rETH Collateral")

        tvl.append(solidity.to_float(rp.call("rocketDepositPool.getBalance")))
        description.append(f"+ {tvl[-1]:12.2f} ETH: Deposit Pool Balance")

        tvl.append(rp.call("rocketMinipoolManager.getActiveMinipoolCount") * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Active Minipools")

        tvl.append(rp.call("rocketMinipoolManager.getMinipoolCountPerStatus", 0, 9999)[1] * 16)
        description.append(f"- {tvl[-1]:12.2f} ETH: Pending Minipool")
        tvl[-1] *= -1

        tvl.append(solidity.to_float(rp.call("rocketNodeStaking.getTotalRPLStake")) * solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice")))
        description.append(f"+ {tvl[-1]:12.2f} ETH: RPL Locked (staked or bonded)")

        description.append("-" * max(len(d) for d in description))
        description.append(f"  {sum(tvl):12.2f} ETH: Total Value Locked")
        description = "```" + "\n".join(description) + "```"
        # send embed with tvl
        embed = Embed(color=self.color)
        embed.set_footer(text="\"Well, it's closer to my earlier calculations than the grafana dashboard.\" - eracpp 2021")
        embed.description = description
        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Random(bot))
