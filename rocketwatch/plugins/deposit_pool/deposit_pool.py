import logging

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
        self.db = AsyncIOMotorClient(cfg["mongodb.uri"]).rocketwatch

    @staticmethod
    def get_deposit_pool_stats() -> Embed:
        multicall: dict[str, int] = {
            res.function_name: res.results[0] for res in rp.multicall.aggregate([
                rp.get_contract_by_name("rocketDepositPool").functions.getBalance(),
                rp.get_contract_by_name("rocketDAOProtocolSettingsDeposit").functions.getMaximumDepositPoolSize(),
                rp.get_contract_by_name("rocketDepositPool").functions.getMaximumDepositAmount(),
                rp.get_contract_by_name("rocketMinipoolQueue").functions.getLength(),
            ]).results
        }

        dp_balance = solidity.to_float(multicall["getBalance"])
        deposit_cap = solidity.to_int(multicall["getMaximumDepositPoolSize"])

        if deposit_cap - dp_balance < 0.01:
            dp_status = "Capacity reached!"
        else:
            fill_perc = dp_balance / deposit_cap
            free_capacity = solidity.to_float(multicall["getMaximumDepositAmount"])
            dp_status = f"Enough space for **{free_capacity:,.2f}** more ETH ({fill_perc:.2%} full)."

        embed = Embed(title="Deposit Pool Stats")
        embed.add_field(name="Current Size", value=f"{dp_balance:,.2f} ETH")
        embed.add_field(name="Maximum Size", value=f"{deposit_cap:,} ETH")
        embed.add_field(name="Status", value=dp_status, inline=False)

        if (queue_length := multicall["getLength"]) > 0:
            embed.description = f"**Minipool Queue** ({queue_length})\n"
            embed.description += Queue.get_minipool_queue(limit=5).description
            queue_capacity = max(queue_length * 31 - dp_balance, 0.0)
            embed.description += f"\nNeed **{queue_capacity:,.2f}** ETH to dequeue all minipools."
        else:
            lines = []
            if (num_leb8 := int(dp_balance // 24)) > 0:
                lines.append(f"**`{num_leb8:>4}`** 8 ETH minipools (24 ETH from DP)")
            if (num_credit := int(dp_balance // 32)) > 0:
                lines.append(f"**`{num_credit:>4}`** credit minipools (32 ETH from DP)")

            if lines:
                embed.add_field(name="Enough For", value="\n".join(lines), inline=False)

        return embed
    
    @staticmethod
    def get_contract_collateral_stats() -> Embed:
        multicall: dict[str, int] = {
            res.function_name: res.results[0] for res in rp.multicall.aggregate([
                rp.get_contract_by_name("rocketTokenRETH").functions.getExchangeRate(),
                rp.get_contract_by_name("rocketTokenRETH").functions.totalSupply(),
                rp.get_contract_by_name("rocketTokenRETH").functions.getCollateralRate(),
                rp.get_contract_by_name("rocketDAOProtocolSettingsNetwork").functions.getTargetRethCollateralRate(),
            ]).results
        }

        total_eth_in_reth: float = multicall["totalSupply"] * multicall["getExchangeRate"] / 10**36
        collateral_rate: float = solidity.to_float(multicall["getCollateralRate"])
        collateral_rate_target: float = solidity.to_float(multicall["getTargetRethCollateralRate"])

        collateral_in_eth = total_eth_in_reth * collateral_rate
        collateral_target_in_eth = total_eth_in_reth * collateral_rate_target
        collateral_used = collateral_in_eth / collateral_target_in_eth

        if collateral_in_eth >= 0.01:
            description = (
                f"**{collateral_in_eth:,.2f}** ETH of liquidity in the rETH contract\n"
                f"**{collateral_used:.2%}** of the **{collateral_target_in_eth:,.2f}** ETH target"
                f" ({collateral_rate:.2%}/{collateral_rate_target:.2%})"
            )
        else:
            description = (
                f"No liquidity in the rETH contract.\n"
                f"Target set to **{collateral_target_in_eth:,.2f}** ETH"
                f" ({collateral_rate_target:.2%} of supply)."
            )

        return Embed(title="rETH Extra Collateral", description=description)
    
    @hybrid_command()
    async def deposit_pool(self, ctx: Context) -> None:
        """Show the current deposit pool status"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        await ctx.send(embed=self.get_deposit_pool_stats())

    @hybrid_command()
    async def reth_extra_collateral(self, ctx: Context) -> None:
        """Show the amount of tokens held in the rETH contract for exit liquidity"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        await ctx.send(embed=self.get_contract_collateral_stats())
        
    async def get_status(self) -> Embed:
        embed = Embed(title=":rocket: Live Deposit Status")

        dp_embed = self.get_deposit_pool_stats()
        embed.description = dp_embed.description
        dp_fields = {field.name: field for field in dp_embed.fields}

        embed.add_field(
            name="DP Balance",
            value=dp_fields["Current Size"].value,
            inline=dp_fields["Current Size"].inline
        )
        embed.add_field(
            name="Max DP Balance",
            value=dp_fields["Maximum Size"].value,
            inline=dp_fields["Maximum Size"].inline
        )
        embed.add_field(name="Deposits", value=dp_fields["Status"].value, inline=dp_fields["Status"].inline)

        collateral_embed = self.get_contract_collateral_stats()
        embed.add_field(name="Withdrawals", value=collateral_embed.description, inline=False)

        return embed

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
