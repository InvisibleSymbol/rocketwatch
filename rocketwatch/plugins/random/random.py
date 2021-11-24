import logging
from datetime import datetime
from io import BytesIO

import humanize
import pytz
from discord import Embed, Color
from discord import File
from discord.ext import commands
from discord_slash import cog_ext

from utils import solidity
from utils.cfg import cfg
from utils.deposit_pool_graph import get_graph
from utils.readable import etherscan_url
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("random")
log.setLevel(cfg["log_level"])


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
        description = "Queues are processed from top to bottom.\n" \
                      "This means that the \"Normal Minipool\" Queue *has to be empty*\n" \
                      "before the \"Full Minipool Refund\" Queue gets processed!\n\n"
        matchings = [
            ["half", "Normal Minipool Queue"],
            ["full", "Full Minipool Refund Queue"],
            ["empty", "Unbonded Minipool FillingQueue"]
        ]
        for category, label in matchings:
            data = minipools[category]
            if data[1]:
                description += f"**{label}:** ({data[0]} Minipools)"
                description += "\n- "
                description += "\n- ".join([etherscan_url(m, f'`{m}`') for m in data[1]])
                if data[0] > 10:
                    description += "\n- ..."
                description += "\n\n"
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
                        value=f"Buffer {percentage_filled}% Full. Enough space for {humanize.intcomma(free_capacity)} more ETH",
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
        await ctx.defer(hidden=is_hidden(ctx))
        tvl = []
        description = []
        eth_price = rp.get_dai_eth_price()

        offset, limit = 0, 500
        minipool_count_per_status = [0, 0, 0, 0, 0]
        while True:
            log.debug(f"getMinipoolCountPerStatus({offset}, {limit})")
            tmp = rp.call("rocketMinipoolManager.getMinipoolCountPerStatus", offset, limit)
            for i in range(len(tmp)):
                minipool_count_per_status[i] += tmp[i]
            if sum(tmp) < limit:
                break
            offset += limit
        log.debug(minipool_count_per_status)
        tvl.append(minipool_count_per_status[2] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Staking Minipools")

        tvl.append(minipool_count_per_status[1] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Pending Minipools")

        tvl.append(minipool_count_per_status[0] * 16)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Unmatched Minipools")

        tvl.append(minipool_count_per_status[3] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Withdrawable Minipools")

        tvl.append(solidity.to_float(rp.call("rocketDepositPool.getBalance")))
        description.append(f"+ {tvl[-1]:12.2f} ETH: Deposit Pool Balance")

        tvl.append(solidity.to_float(w3.eth.getBalance(rp.get_address_by_name("rocketTokenRETH"))))
        description.append(f"+ {tvl[-1]:12.2f} ETH: rETH Extra Collateral")

        description.append("Total ETH Locked".center(max(len(d) for d in description), "-"))
        eth_tvl = sum(tvl)
        # get eth tvl in dai
        dai_eth_tvl = eth_tvl * eth_price
        description.append(f"  {eth_tvl:12.2f} ETH ({humanize.intword(dai_eth_tvl)} DAI)")

        tvl.append(solidity.to_float(rp.call("rocketNodeStaking.getTotalRPLStake")))
        description.append(f"+ {tvl[-1]:12.2f} RPL: Staked or Bonded RPL")
        # convert rpl to eth for correct tvl calcuation
        tvl[-1] *= solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))

        description.append("Total Value Locked".center(max(len(d) for d in description), "-"))
        total_tvl = sum(tvl)
        dai_total_tvl = total_tvl * eth_price
        description.append(f"  {total_tvl:12.2f} ETH ({humanize.intword(dai_total_tvl)} DAI)")

        description = "```diff\n" + "\n".join(description) + "```"
        # send embed with tvl
        embed = Embed(color=self.color)
        embed.set_footer(text="\"Well, it's closer to my earlier calculations than the grafana dashboard.\" - eracpp 2021")
        embed.description = description
        await ctx.send(embed=embed, hidden=is_hidden(ctx))


def setup(bot):
    bot.add_cog(Random(bot))
