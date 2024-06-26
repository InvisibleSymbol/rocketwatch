import logging

import math
import requests
from typing import Optional
from dataclasses import dataclass
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, resolve_ens
from utils.reporter import report_error
from utils.rocketpool import rp

log = logging.getLogger("effective_rpl")
log.setLevel(cfg["log_level"])


class PatchesAPI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @dataclass
    class RewardEstimate:
        interval: int
        start_time: int
        data_time: int
        end_time: int
        rpl_rewards: float
        eth_rewards: float

    @staticmethod
    async def get_estimated_rewards(ctx: Context, address: str) -> Optional[RewardEstimate]:
        try:
            patches_res = requests.get(f"https://sprocketpool.net/api/node/{address}").json()
        except Exception as e:
            await report_error(ctx, e)
            await ctx.send("Error fetching node data from SprocketPool API. Blame Patches.")
            return None

        rpl_rewards: Optional[int] = patches_res[address].get('collateralRpl')
        eth_rewards: Optional[int] = patches_res[address].get('smoothingPoolEth')

        if (rpl_rewards is None) or (eth_rewards is None):
            await ctx.send("No data found for this node.")
            return None

        interval_time = rp.call("rocketDAOProtocolSettingsRewards.getRewardsClaimIntervalTime")

        return PatchesAPI.RewardEstimate(
            interval=patches_res['interval'],
            start_time=patches_res["startTime"],
            data_time=patches_res['time'],
            end_time=patches_res["startTime"] + interval_time,
            rpl_rewards=solidity.to_float(rpl_rewards),
            eth_rewards=solidity.to_float(eth_rewards),
        )

    @staticmethod
    def create_embed(title: str, rewards: RewardEstimate) -> Embed:
        embed = Embed()
        embed.title = title
        embed.description = (
            f"Values based on data from <t:{rewards.data_time}:R> (<t:{rewards.data_time}>).\n"
            f"This is for interval {rewards.interval}, which ends <t:{rewards.end_time}:R> (<t:{rewards.end_time}>)."
        )
        embed.add_field(name="RPL Staking:", value=f"{rewards.rpl_rewards:,.3f} RPL")
        embed.add_field(name="Smoothing Pool:", value=f"{rewards.eth_rewards:,.3f} ETH")
        return embed

    @hybrid_command()
    async def upcoming_rewards(self, ctx: Context, node_address: str, extrapolate: bool = True):
        await ctx.defer(ephemeral=True)
        display_name, address = await resolve_ens(ctx, node_address)
        if display_name is None:
            return

        rewards = await self.get_estimated_rewards(ctx, address)
        if rewards is None:
            return

        if extrapolate:
            extrapolation_factor = (rewards.end_time - rewards.start_time) / (rewards.data_time - rewards.start_time)
            rewards.rpl_rewards *= extrapolation_factor
            rewards.eth_rewards *= extrapolation_factor

        modifier = "Projected" if extrapolate else "Estimated Ongoing"
        title = f"{modifier} Rewards for {display_name}"
        await ctx.send(embed=self.create_embed(title, rewards))

    @hybrid_command()
    async def simulate_rewards(self, ctx: Context, node_address: str, rpl_stake: int):
        await ctx.defer(ephemeral=True)
        display_name, address = await resolve_ens(ctx, node_address)
        if display_name is None:
            return

        rewards = await self.get_estimated_rewards(ctx, address)
        if rewards is None:
            return

        extrap_factor = (rewards.end_time - rewards.start_time) / (rewards.data_time - rewards.start_time)
        rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        borrowed_eth = solidity.to_float(rp.call("rocketNodeStaking.getNodeETHMatched", address))
        current_rpl_stake = solidity.to_float(rp.call("rocketNodeStaking.getNodeRPLStake", address))

        def rpip_30_weight(staked_rpl: float) -> float:
            rpl_value = staked_rpl * rpl_ratio
            collateral_ratio = rpl_value / borrowed_eth
            if collateral_ratio < 0.1:
                return 0.0
            elif collateral_ratio <= 0.15:
                return 100 * rpl_value
            else:
                return (13.6137 + 2 * math.log(100 * collateral_ratio - 13)) * borrowed_eth

        weight_factor = rpip_30_weight(rpl_stake) / rpip_30_weight(current_rpl_stake)
        rewards.rpl_rewards *= extrap_factor * weight_factor
        rewards.eth_rewards *= extrap_factor

        title = f"Simulated Rewards for {display_name} ({rpl_stake:,} RPL Staked)"
        await ctx.send(embed=self.create_embed(title, rewards))


async def setup(bot):
    await bot.add_cog(PatchesAPI(bot))
