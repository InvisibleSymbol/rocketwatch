import logging

from discord.commands import slash_command
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, etherscan_url
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("defi")
log.setLevel(cfg["log_level"])


class DeFi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @slash_command(guild_ids=guilds)
    async def curve(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        e.title = "Curve Pool"
        reth_r, wsteth_r = rp.call("curvePool.get_balances")
        # token amounts
        reth = solidity.to_float(reth_r)
        wsteth = solidity.to_float(wsteth_r)
        # token values
        reth_v = solidity.to_float(rp.call("rocketTokenRETH.getEthValue", reth_r))
        wsteth_v = solidity.to_float(rp.call("wstETHToken.getStETHByWstETH", wsteth_r))
        # token shares
        reth_s = reth / (reth + wsteth)
        wsteth_s = wsteth / (reth + wsteth)
        e.add_field(
            name="rETH Locked",
            value=f"`{reth:,.2f} rETH ({reth_s:.0%})`",
        )
        e.add_field(
            name="wstETH Locked",
            value=f"`{wsteth:,.2f} wstETH ({wsteth_s:.0%})`",
        )
        total_locked = reth_v + wsteth_v
        total_locked_usd = total_locked * rp.get_dai_eth_price()
        e.add_field(
            name="Total Value Locked",
            value=f"`{total_locked:,.2f} ETH ({total_locked_usd:,.2f} DAI)`",
            inline=False,
        )
        expected_ratio = (reth_v / reth) / (wsteth_v / wsteth)
        actual_ratio = solidity.to_float(rp.call("curvePool.get_dy", 0, 1, w3.toWei(1, "ether")))
        premium = (actual_ratio / expected_ratio) - 1
        e.add_field(
            name="Current rETH => wstETH Premium",
            value=f"`{premium:.2%}`",
            inline=False,
        )
        token_name = rp.call("curvePool.symbol")
        link = etherscan_url(rp.get_address_by_name("curvePool"), token_name)
        e.add_field(
            name="Contract Address",
            value=link,
        )
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))

    @slash_command(guild_ids=guilds)
    async def yearn(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        e.title = "Yearn Pool"
        deposit_limit = solidity.to_float(rp.call("yearnPool.depositLimit"))
        deposited = solidity.to_float(rp.call("yearnPool.totalAssets"))
        asset_name = rp.call("curvePool.symbol")
        e.add_field(
            name="Deposit Limit Status",
            value=f"`{deposited:,.2f}/{deposit_limit:,.2f} {asset_name}`",
        )
        reth_r, wsteth_r = rp.call("curvePool.get_balances")
        # token values
        reth_v = solidity.to_float(rp.call("rocketTokenRETH.getEthValue", reth_r))
        wsteth_v = solidity.to_float(rp.call("wstETHToken.getStETHByWstETH", wsteth_r))
        yearn_locked = (reth_v + wsteth_v) * (rp.call("yearnPool.totalAssets") / rp.call("curvePool.totalSupply"))
        yearn_locked_usd = yearn_locked * rp.get_dai_eth_price()
        e.add_field(
            name="Total Value Locked",
            value=f"`{yearn_locked:,.2f} ETH ({yearn_locked_usd:,.2f} DAI)`",
            inline=False,
        )
        token_name = rp.call("yearnPool.symbol")
        link = etherscan_url(rp.get_address_by_name("yearnPool"), token_name)
        e.add_field(
            name="Contract Address",
            value=link,
        )
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(DeFi(bot))
