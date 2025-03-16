import logging

import humanize
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from plugins.queue.queue import Queue
from utils.status import StatusPlugin
from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.visibility import is_hidden_weak

log = logging.getLogger("deposit_pool")
log.setLevel(cfg["log_level"])


class DepositPool(StatusPlugin):
    def __init__(self, bot: RocketWatch):
        super().__init__(bot)
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).rocketwatch

    @staticmethod
    def get_deposit_pool_stats() -> Embed:
        multicall: dict[str, int] = {
            res.function_name: res.results[0] for res in rp.multicall.aggregate([
                rp.get_contract_by_name("rocketDepositPool").functions.getBalance(),
                rp.get_contract_by_name("rocketDAOProtocolSettingsDeposit").functions.getMaximumDepositPoolSize(),
                rp.get_contract_by_name("rocketDepositPool").functions.getMaximumDepositAmount(),
                rp.get_contract_by_name("rocketMinipoolQueue").functions.getTotalLength(),
            ]).results
        }

        dp_balance = solidity.to_float(multicall["getBalance"])
        deposit_cap = solidity.to_int(multicall["getMaximumDepositPoolSize"])

        if deposit_cap - dp_balance < 0.01:
            dp_status = "Capacity reached!"
        else:
            fill_perc = dp_balance / deposit_cap
            free_capacity = solidity.to_float(multicall["getMaximumDepositAmount"])
            dp_status = f"{fill_perc:.2%} full, enough space for **{free_capacity:.2f}** more ETH."

        embed = Embed(title="Deposit Pool Stats")
        embed.add_field(name="Current Size", value=f"{dp_balance:.2f} ETH")
        embed.add_field(name="Maximum Size", value=f"{deposit_cap:,} ETH")
        embed.add_field(name="Status", value=dp_status, inline=False)

        queue_length = multicall["getTotalLength"]
        if queue_length > 0:
            embed.description = Queue.get_minipool_queue(limit=5).description
            queue_capacity = max(queue_length * 31 - dp_balance, 0.0)
            embed.description += f"\nNeed **{queue_capacity:.2f}** more ETH to dequeue all minipools."
        else:
            lines = []
            if (num_leb8 := dp_balance // 24) > 0:
                lines.append(f"**`{num_leb8:>4.0f}`** 8 ETH minipools (24 ETH from DP)")
            if (num_credit := dp_balance // 32) > 0:
                lines.append(f"**`{num_credit:>4.0f}`** credit minipools (32 ETH from DP)")

            if lines:
                embed.add_field(name="Enough For", value="\n".join(lines), inline=False)

        return embed

    @hybrid_command()
    async def dp(self, ctx: Context):
        """Deposit Pool Stats"""
        await self.deposit_pool(ctx)

    @hybrid_command()
    async def deposit_pool(self, ctx: Context):
        """Deposit Pool Stats"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = self.get_deposit_pool_stats()
        await ctx.send(embed=embed)

    @staticmethod
    async def get_status_message() -> Embed:
        embed = DepositPool.get_deposit_pool_stats()
        embed.title = ":rocket: Live Deposit Pool Status"
        return embed

    @hybrid_command()
    async def reth_extra_collateral(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        current_reth_value = solidity.to_float(rp.call("rocketTokenRETH.getEthValue", rp.call("rocketTokenRETH.totalSupply")))
        current_collateral_rate = solidity.to_float(rp.call("rocketTokenRETH.getCollateralRate"))
        current_collateral_in_eth = current_reth_value * current_collateral_rate
        collateral_rate_target = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getTargetRethCollateralRate"))
        collateral_target_in_eth = current_reth_value * collateral_rate_target
        collateral_used = current_collateral_in_eth / collateral_target_in_eth

        e = Embed()
        e.title = "rETH Extra Collateral"

        e.description = f"Current Extra Collateral stored in the rETH Contract is **{humanize.intcomma(round(current_collateral_in_eth, 2))}** ETH\n" \
                        f"That is **{collateral_used:.2%}** of the configured target of **{humanize.intcomma(round(collateral_target_in_eth, 2))}** ETH ({current_collateral_rate:.2%}/{collateral_rate_target:.2%})\n"

        await ctx.send(embed=e)

    @hybrid_command()
    async def atlas_queue(self, ctx):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        e = Embed()
        e.title = "Atlas Queue Stats"

        data = await self.db.minipools_new.aggregate([
            {
                '$match': {
                    'status'        : 'initialised',
                    'deposit_amount': {
                        '$gt': 1
                    }
                }
            }, {
                '$group': {
                    '_id'     : 'total',
                    'value'   : {
                        '$sum': {
                            '$subtract': [
                                '$deposit_amount', 1
                            ]
                        }
                    },
                    'count'   : {
                        '$sum': 1
                    },
                    'count_16': {
                        '$sum': {
                            '$floor': {
                                '$divide': [
                                    '$node_deposit_balance', 16
                                ]
                            }
                        }
                    }
                }
            }
        ]).to_list(None)

        total = int(data[0]['value'])
        count = data[0]['count']
        count_16 = int(data[0]['count_16'])
        count_8 = count - count_16

        e.description = f"Amount deposited into deposit pool by queued minipools: **{total} ETH**\n" \
                        f"Non-credit minipools in the queue: **{count}** (16 ETH: **{count_16}**, 8 ETH: **{count_8}**)\n" \

        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(DepositPool(bot))
