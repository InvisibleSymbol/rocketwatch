import logging

import humanize
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from plugins.queue import queue
from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.visibility import is_hidden

log = logging.getLogger("despoit_pool")
log.setLevel(cfg["log_level"])


async def get_dp() -> Embed:
    e = Embed()
    e.title = "Deposit Pool Stats"

    deposit_pool = solidity.to_float(rp.call("rocketDepositPool.getBalance"))
    e.add_field(name="Current Size:", value=f"{humanize.intcomma(round(deposit_pool, 2))} ETH")

    deposit_cap = solidity.to_int(rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositPoolSize"))
    e.add_field(name="Maximum Size:", value=f"{humanize.intcomma(deposit_cap)} ETH")

    if deposit_cap - deposit_pool < 0.01:
        e.add_field(name="Status:", value="Deposit Pool Cap Reached!", inline=False)
    else:
        percentage_filled = round(deposit_pool / deposit_cap * 100, 2)
        free_capacity = solidity.to_float(rp.call("rocketDepositPool.getMaximumDepositAmount"))
        free_capacity = round(free_capacity, 2)
        e.add_field(name="Status:",
                    value=f"Buffer {percentage_filled}% Full. Enough space for **{humanize.intcomma(free_capacity)}** more ETH",
                    inline=False)

    queue_length = rp.call("rocketMinipoolQueue.getTotalLength")
    if queue_length > 0:
        e.description = queue.get_queue(l=5)
        e.description += f"\nNeed **{humanize.intcomma(max(round(queue_length * 31 - deposit_pool,2), 0))}** more ETH to dequeue all minipools"
    elif deposit_pool // 16 > 0:
        e.add_field(name="Enough For:",
                    value=f"**`{deposit_pool // 16:>4.0f}`** 16 ETH Minipools (16 ETH from DP)" +
                          (f"\n**`{deposit_pool // 24:>4.0f}`** 8 ETH Minipools (24 ETH from DP)" if deposit_pool // 24 > 0 else "") +
                          (f"\n**`{deposit_pool // 32:>4.0f}`** Credit Minipools (32 ETH from DP)" if deposit_pool // 32 > 0 else ""),
                    inline=False)

    return e


class DepositPool(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @hybrid_command()
    async def dp(self, ctx: Context):
        """Deposit Pool Stats"""
        await self.deposit_pool(ctx)

    @hybrid_command()
    async def deposit_pool(self, ctx: Context):
        """Deposit Pool Stats"""
        await ctx.defer(ephemeral=is_hidden(ctx))
        embed = await get_dp()
        await ctx.send(embed=embed)

    @hybrid_command()
    async def reth_extra_collateral(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden(ctx))

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
        await ctx.defer(ephemeral=is_hidden(ctx))

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
