import asyncio
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
from utils.readable import etherscan_url, uptime
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
        rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        rpl_address = rp.get_address_by_name("rocketTokenRPL")
        minipool_count_per_status = rp.get_minipool_count_per_status()

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
        description.append(f"+ {tvl[-1]:12.2f} RPL: Staked RPL")
        # convert rpl to eth for correct tvl calcuation
        tvl[-1] *= rpl_price

        tvl.append(solidity.to_float(rp.call("rocketVault.balanceOfToken", "rocketDAONodeTrustedActions", rpl_address)))
        description.append(f"+ {tvl[-1]:12.2f} RPL: oDAO Bonded RPL")
        # convert rpl to eth for correct tvl calculation
        tvl[-1] *= rpl_price

        tvl.append(solidity.to_float(rp.call("rocketVault.balanceOfToken", "rocketAuctionManager", rpl_address)))
        description.append(f"+ {tvl[-1]:12.2f} RPL: Slashed RPL ")
        # convert rpl to eth for correct tvl calculation
        tvl[-1] *= rpl_price

        description.append("Total Value Locked".center(max(len(d) for d in description), "-"))
        total_tvl = sum(tvl)
        dai_total_tvl = total_tvl * eth_price
        description.append(f"  {total_tvl:12.2f} ETH ({humanize.intword(dai_total_tvl)} DAI)")

        description = "```diff\n" + "\n".join(description) + "```"
        # send embed with tvl
        embed = Embed(color=self.color)
        embed.set_footer(text="\"that looks good to me\" - kanewallmann 2021")
        embed.description = description
        await ctx.send(embed=embed, hidden=is_hidden(ctx))

    @cog_ext.cog_slash(guild_ids=guilds)
    async def current_rETH_apr(self, ctx):
        await ctx.defer(hidden=is_hidden(ctx))
        e = Embed(color=self.color)
        e.title = "Current Estimated rETH APR"

        # get update blocks
        current_update_block = rp.call("rocketNetworkBalances.getBalancesBlock")
        previous_update_block = rp.call("rocketNetworkBalances.getBalancesBlock", block=current_update_block - 1)

        # get timestamps of blocks
        current_update_timestamp = w3.eth.get_block(current_update_block).timestamp
        previous_update_timestamp = w3.eth.get_block(previous_update_block).timestamp

        # get ratios after and before current update block
        current_ratio = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate"))
        previous_ratio = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate", block=current_update_block - 1))

        # calculate the percentage increase in ratio over 24 hours
        ratio_increase = (current_ratio - previous_ratio) / (
                current_update_timestamp - previous_update_timestamp) * 24 * 60 * 60

        # turn into yearly percentage
        yearly_percentage = ratio_increase * 365

        e.description = "**Note**: In the early stages of rETH the calculated APR might be lower than expected!\n" \
                        "This is caused by many things, such as a high stale ETH ratio lowering the earned rewards per ETH" \
                        " or a low Minipool count combined with bad Luck simply resulting in lower rewards for a day. "

        e.add_field(name="Latest rETH/ETH Updates:", value=f"`{current_ratio:.6f}` on <t:{current_update_timestamp}>\n"
                                                           f"`{previous_ratio:.6f}` on <t:{previous_update_timestamp}>")

        e.add_field(name="APR based on rETH/ETH Ratio Change:", value=f"{yearly_percentage:.3%}", inline=False)

        e.set_footer(
            text=f"Duration between used ratio updates: {uptime(current_update_timestamp - previous_update_timestamp)}")

        await ctx.send(embed=e, hidden=is_hidden(ctx))

    @cog_ext.cog_slash(guild_ids=guilds)
    async def rewards(self, ctx):
        await ctx.defer(hidden=is_hidden(ctx))
        e = Embed(color=self.color)
        e.title = "Reward Period Stats"
        # get rpl price in dai
        rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        rpl_price = rpl_ratio * rp.get_dai_eth_price()

        # get reward period amount
        total_reward_pool = solidity.to_float(rp.call("rocketRewardsPool.getClaimIntervalRewardsTotal"))
        total_reward_pool_eth = humanize.intcomma(total_reward_pool * rpl_ratio, 2)
        total_reward_pool_dai = humanize.intword(total_reward_pool * rpl_price)
        total_reward_pool_formatted = humanize.intcomma(total_reward_pool, 2)
        e.add_field(name="Allocated RPL:",
                    value=f"{total_reward_pool_formatted} RPL "
                          f"(worth {total_reward_pool_dai} DAI or {total_reward_pool_eth} ETH)",
                    inline=False)

        # get reward period start
        reward_start = rp.call("rocketRewardsPool.getClaimIntervalTimeStart")
        e.add_field(name="Period Start:", value=f"<t:{reward_start}>")

        # show duration left
        reward_duration = rp.call("rocketRewardsPool.getClaimIntervalTime")
        reward_end = reward_start + reward_duration
        left_over_duration = max(reward_end - w3.eth.getBlock('latest').timestamp, 0)
        e.add_field(name="Duration Left:", value=f"{uptime(left_over_duration)}")

        claiming_contracts = [
            ["rocketClaimNode", "Node Operator Rewards"],
            ["rocketClaimTrustedNode", "oDAO Member Rewards"],
            ["rocketClaimDAO", "pDAO Rewards"]
        ]

        distribution = "```\n"
        for contract, name in claiming_contracts:
            await asyncio.sleep(0.01)
            percentage = solidity.to_float(rp.call("rocketRewardsPool.getClaimingContractPerc", contract))
            amount = solidity.to_float(rp.call("rocketRewardsPool.getClaimingContractAllowance", contract))
            amount_formatted = humanize.intcomma(amount, 2)
            distribution += f"{name}:\n\tAllocated:\t{amount_formatted:>10} RPL ({percentage:.0%})\n"

            # show how much was already claimed
            claimed = solidity.to_float(rp.call(f"rocketRewardsPool.getClaimingContractTotalClaimed", contract))
            claimed_formatted = humanize.intcomma(claimed, 2)

            # percentage already claimed
            claimed_percentage = claimed / amount
            distribution += f"\tClaimed:\t  {claimed_formatted:>10} RPL ({claimed_percentage:.0%})\n"

        distribution += "```"
        e.add_field(name="Distribution", value=distribution, inline=False)

        # show how much a node operator can claim with 10% (1.6 ETH) collateral and 150% (24 ETH) collateral
        node_operator_rewards = solidity.to_float(rp.call("rocketRewardsPool.getClaimingContractAllowance", "rocketClaimNode"))
        total_rpl_staked = solidity.to_float(rp.call("rocketNetworkPrices.getEffectiveRPLStake"))
        reward_per_staked_rpl = node_operator_rewards / total_rpl_staked

        # get minimum collateralized minipool
        reward_10_percent = reward_per_staked_rpl * (1.6 / rpl_ratio)
        reward_10_percent_eth = humanize.intcomma(reward_10_percent * rpl_ratio, 2)
        reward_10_percent_dai = humanize.intcomma(reward_10_percent * rpl_price, 2)

        # get maximum collateralized minipool
        reward_150_percent = reward_per_staked_rpl * (24 / rpl_ratio)
        reward_150_percent_eth = humanize.intcomma(reward_150_percent * rpl_ratio, 2)
        reward_150_percent_dai = humanize.intcomma(reward_150_percent * rpl_price, 2)

        e.add_field(name="Current Rewards per Minipool:",
                    value=f"```\n"
                          f"10% collateralized Minipool:\n\t{humanize.intcomma(reward_10_percent, 2):>6} RPL"
                          f" (worth {reward_10_percent_eth} ETH or"
                          f" {reward_10_percent_dai} DAI)\n"
                          f"150% collateralized Minipool:\n\t{humanize.intcomma(reward_150_percent, 2):>6} RPL"
                          f" (worth {reward_150_percent_eth} ETH or"
                          f" {reward_150_percent_dai} DAI)\n"
                          f"```",
                    inline=False)

        # calculate current APR for node operators
        apr = reward_per_staked_rpl / (reward_duration / 60 / 60 / 24) * 365
        e.add_field(name="Node Operator RPL Rewards APR:", value=f"{apr:.2%}")

        # send embed
        await ctx.send(embed=e, hidden=is_hidden(ctx))


def setup(bot):
    bot.add_cog(Random(bot))
