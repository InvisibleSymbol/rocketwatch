import asyncio
import logging
from statistics import median

import humanize
from discord import Embed, Color
from discord.commands import slash_command
from discord.ext import commands

from utils import solidity
from utils.cfg import cfg
from utils.embeds import etherscan_url
from utils.readable import uptime
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.thegraph import get_unclaimed_rpl_reward_nodes, get_unclaimed_rpl_reward_odao, get_claims_current_period
from utils.visibility import is_hidden

log = logging.getLogger("Rewards")
log.setLevel(cfg["log_level"])


class Rewards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def rewards(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
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
            distribution += f"{name} ({percentage:.0%}):\n\tAllocated:\t{amount_formatted:>10} RPL\n"

            # show how much was already claimed
            claimed = solidity.to_float(
                rp.call(
                    'rocketRewardsPool.getClaimingContractTotalClaimed', contract
                )
            )

            claimed_formatted = humanize.intcomma(claimed, 2)

            # percentage already claimed
            claimed_percentage = claimed / amount
            distribution += f"\t├Claimed:\t{claimed_formatted:>11} RPL ({claimed_percentage:.0%})\n"

            if "Node" in contract:
                if "oDAO Member" in name:
                    waiting_for_claims, potential_rollover = get_unclaimed_rpl_reward_odao()
                else:
                    waiting_for_claims, potential_rollover = get_unclaimed_rpl_reward_nodes()
                waiting_percentage = waiting_for_claims / amount
                waiting_for_claims = humanize.intcomma(waiting_for_claims, 2)
                distribution += f"\t├Pending:\t{waiting_for_claims:>11} RPL ({waiting_percentage:.0%})\n"
                rollover_percentage = potential_rollover / amount
                potential_rollover = humanize.intcomma(potential_rollover, 2)
                distribution += f"\t├Rollover*:\t{potential_rollover:>9} RPL ({rollover_percentage:.0%})\n"

            # reverse distribution string
            distribution = distribution[::-1]
            # replace (now first) last occurrence of ├ with └
            distribution = distribution.replace("├", "└", 1)
            # reverse again
            distribution = distribution[::-1]

        distribution += "```"
        distribution += "* Rollover is the estimated amount of RPL that will be carried over into the next period based on the currently pending claims."
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

        # calculate current APR for node operators
        apr = reward_per_staked_rpl / (reward_duration / 60 / 60 / 24) * 365
        e.add_field(name="Node Operator RPL Rewards APR:", value=f"{apr:.2%}")

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

        # show Rewards per oDAO Member
        total_odao_members = rp.call("rocketDAONodeTrusted.getMemberCount")
        odao_members_rewards = solidity.to_float(rp.call("rocketRewardsPool.getClaimingContractAllowance", "rocketClaimTrustedNode"))
        rewards_per_odao_member = odao_members_rewards / total_odao_members
        rewards_per_odao_member_eth = humanize.intcomma(rewards_per_odao_member * rpl_ratio, 2)
        rewards_per_odao_member_dai = humanize.intcomma(rewards_per_odao_member * rpl_price, 2)

        e.add_field(name="Current Rewards per oDAO Member:",
                    value=f"```\n"
                          f"{humanize.intcomma(rewards_per_odao_member, 2):>6} RPL"
                          f" (worth {rewards_per_odao_member_eth} ETH or"
                          f" {rewards_per_odao_member_dai} DAI)\n"
                          f"```",
                    inline=False)
        # send embed
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))

    @slash_command(guild_ids=guilds)
    async def median_claim(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(color=self.color)
        e.title = "Median Claim for this Period"
        counts = get_claims_current_period()
        # top 5 claims
        top_claims = sorted(counts, key=lambda x: int(x["amount"]), reverse=True)[:5]
        top_claims_str = [
            f"{i + 1}. {etherscan_url(w3.toChecksumAddress(claim['claimer']))}:"
            f" {solidity.to_float(claim['amount']):.2f} RPL"
            f" (worth {solidity.to_float(claim['ethAmount']):.2f} ETH)"
            for i, claim in enumerate(top_claims)
        ]

        e.add_field(name="Top 5 Claims", value="\n".join(top_claims_str), inline=False)
        # show median claim
        rpl_amounts = sorted([solidity.to_float(claim["amount"]) for claim in counts])
        median_claim = humanize.intcomma(median(rpl_amounts), 2)
        eth_amounts = sorted([solidity.to_float(claim["ethAmount"]) for claim in counts])
        median_claim_eth = humanize.intcomma(median(eth_amounts), 2)
        e.add_field(name="Median Claim:", value=f"{median_claim} RPL (worth {median_claim_eth} ETH)", inline=False)

        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(Rewards(bot))
