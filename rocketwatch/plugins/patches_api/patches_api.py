import logging
import requests
import numpy as np
import matplotlib.pyplot as plt

from io import BytesIO
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from typing import Optional
from dataclasses import dataclass

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, resolve_ens
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.get_nearest_block import get_block_by_timestamp


log = logging.getLogger("effective_rpl")
log.setLevel(cfg["log_level"])


class PatchesAPI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @dataclass
    class RewardEstimate:
        address: str
        interval: int
        start_time: int
        data_time: int
        data_block: int
        end_time: int
        rpl_rewards: float
        eth_rewards: float
        system_weight: float

    @staticmethod
    async def get_estimated_rewards(ctx: Context, address: str) -> Optional[RewardEstimate]:
        if not rp.call("rocketNodeManager.getNodeExists", address):
            await ctx.send(f"{address} is not a registered node.")
            return None

        try:
            patches_res = requests.get(f"https://sprocketpool.net/api/node/{address}").json()
        except Exception as e:
            await report_error(ctx, e)
            await ctx.send("Error fetching node data from SprocketPool API. Blame Patches.")
            return None

        data_block, _ = get_block_by_timestamp(patches_res["time"])
        rpl_rewards: int = patches_res[address].get("collateralRpl", 0)
        eth_rewards: int = patches_res[address].get("smoothingPoolEth", 0)
        interval_time = rp.call("rocketDAOProtocolSettingsRewards.getRewardsClaimIntervalTime", block=data_block)

        return PatchesAPI.RewardEstimate(
            address=address,
            interval=patches_res["interval"],
            start_time=patches_res["startTime"],
            data_time=patches_res["time"],
            data_block=data_block,
            end_time=patches_res["startTime"] + interval_time,
            rpl_rewards=solidity.to_float(rpl_rewards),
            eth_rewards=solidity.to_float(eth_rewards),
            system_weight=solidity.to_float(patches_res["totalNodeWeight"])
        )

    @staticmethod
    def create_embed(title: str, rewards: RewardEstimate) -> Embed:
        embed = Embed()
        embed.title = title
        embed.description = (
            f"Values based on data from <t:{rewards.data_time}:R> (<t:{rewards.data_time}>).\n"
            f"This is for interval {rewards.interval}, which ends <t:{rewards.end_time}:R> (<t:{rewards.end_time}>)."
        )
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
            registration_time = rp.call("rocketNodeManager.getNodeRegistrationTime", address)
            reward_start_time = max(registration_time, rewards.start_time)
            proj_factor = (rewards.end_time - reward_start_time) / (rewards.data_time - reward_start_time)
            rewards.rpl_rewards *= proj_factor
            rewards.eth_rewards *= proj_factor

        modifier = "Projected" if extrapolate else "Estimated Ongoing"
        title = f"{modifier} Rewards for {display_name}"
        embed = self.create_embed(title, rewards)
        embed.add_field(name="RPL Staking:", value=f"{rewards.rpl_rewards:,.3f} RPL")
        embed.add_field(name="Smoothing Pool:", value=f"{rewards.eth_rewards:,.3f} ETH")
        await ctx.send(embed=embed)

    @hybrid_command()
    async def simulate_rewards(
            self,
            ctx: Context,
            node_address: str,
            rpl_stake: int,
            num_leb8: int = 0,
            num_eb16: int = 0
    ):
        await ctx.defer(ephemeral=True)
        display_name, address = await resolve_ens(ctx, node_address)
        if display_name is None:
            return

        rewards = await self.get_estimated_rewards(ctx, address)
        if rewards is None:
            return

        data_block: int = rewards.data_block
        rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice", block=data_block))
        actual_borrowed_eth = solidity.to_float(rp.call("rocketNodeStaking.getNodeETHMatched", address, block=data_block))
        actual_rpl_stake = solidity.to_float(rp.call("rocketNodeStaking.getNodeRPLStake", address, block=data_block))

        inflation_rate: int = rp.call("rocketTokenRPL.getInflationIntervalRate", block=data_block)
        inflation_interval: int = rp.call("rocketTokenRPL.getInflationIntervalTime", block=data_block)
        num_inflation_intervals: int = (rewards.end_time - rewards.start_time) // inflation_interval
        total_supply: int = rp.call("rocketTokenRPL.totalSupply", block=data_block)

        period_inflation: int = total_supply
        for i in range(num_inflation_intervals):
            period_inflation = solidity.to_int(period_inflation * inflation_rate)
        period_inflation -= total_supply

        def rpip_30_weight(_stake: float, _borrowed_eth: float) -> float:
            rpl_value = _stake * rpl_ratio
            collateral_ratio = rpl_value / _borrowed_eth
            if collateral_ratio < 0.1:
                return 0.0
            elif collateral_ratio <= 0.15:
                return 100 * rpl_value
            else:
                return (13.6137 + 2 * np.log(100 * collateral_ratio - 13)) * _borrowed_eth

        def rewards_at(_stake: float, _borrowed_eth: float) -> float:
            weight = rpip_30_weight(_stake, _borrowed_eth)
            base_weight = rpip_30_weight(actual_rpl_stake, _borrowed_eth)
            new_system_weight = rewards.system_weight + weight - base_weight
            return solidity.to_float(0.7 * period_inflation * weight / new_system_weight)

        fig, ax = plt.subplots(figsize=(5, 2.5))
        ax.grid()

        x_min = min(rpl_stake / 2, actual_rpl_stake / 2)
        x_max = max(rpl_stake * 2, actual_rpl_stake * 5)
        ax.set_xlim((x_min, x_max))

        def draw_reward_curve(_color: str, _line_style: str, _borrowed_eth: float) -> None:
            x = np.arange(x_min, x_max, 10, dtype=int)
            y = np.array([rewards_at(x, _borrowed_eth) for x in x])
            ax.plot(x, y, color=_color, linestyle=_line_style)

            projected_rewards = rewards_at(actual_rpl_stake, _borrowed_eth)
            simulated_rewards = rewards_at(rpl_stake, _borrowed_eth)

            ax.plot(actual_rpl_stake, projected_rewards, "o", color="#eb8e55", label="current")
            ax.annotate(
                f"{projected_rewards:.2f}",
                (actual_rpl_stake, projected_rewards),
                textcoords="offset points",
                xytext=(5, -10 if projected_rewards > 0 else 5),
                ha="left"
            )

            ax.plot(rpl_stake, simulated_rewards, "o", color="darkred", label="simulated")
            ax.annotate(
                f"{simulated_rewards:.2f}",
                (rpl_stake, simulated_rewards),
                textcoords="offset points",
                xytext=(5, -10 if simulated_rewards > 0 else 5),
                ha="left"
            )

        draw_reward_curve("#eb8e55", "solid", actual_borrowed_eth)

        borrowed_eth = (24 * num_leb8) + (16 * num_eb16)
        if borrowed_eth > 0:
            draw_reward_curve("darkred", "dashed", borrowed_eth)

        def formatter(_x, _pos) -> str:
            if _x < 1000:
                return f"{_x:.0f}"
            elif _x < 10_000:
                return f"{(_x / 1000):.1f}k"
            elif _x < 1_000_000:
                return f"{(_x / 1000):.0f}k"
            else:
                return f"{(_x / 1_000_000):.1f}m"

        ax.set_xlabel("rpl stake")
        ax.set_ylabel("rewards")
        ax.xaxis.set_major_formatter(formatter)

        handles, labels = ax.get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        plt.legend(by_label.values(), by_label.keys(), loc="lower right")
        fig.tight_layout()

        img = BytesIO()
        fig.savefig(img, format="png")
        img.seek(0)
        plt.close()

        sim_info = f"{rpl_stake:,} RPL"
        if num_leb8 > 0:
            sim_info += f", {num_leb8} x 8 ETH"
        if num_eb16 > 0:
            sim_info += f", {num_eb16} x 16 ETH"

        title = f"Simulated RPL Rewards for {display_name} ({sim_info})"
        embed = self.create_embed(title, rewards)
        embed.set_image(url="attachment://graph.png")

        f = File(img, filename="graph.png")
        await ctx.send(embed=embed, files=[f])
        img.close()


async def setup(bot):
    await bot.add_cog(PatchesAPI(bot))
