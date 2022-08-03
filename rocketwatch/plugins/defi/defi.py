import logging

import aiohttp
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, el_explorer_url
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.visibility import is_hidden, is_hidden_weak

log = logging.getLogger("defi")
log.setLevel(cfg["log_level"])


class DeFi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def curve(self, ctx: Context):
        """
        Show stats of the curve pool
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
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
        # rETH => wstETH premium
        expected_ratio = (reth_v / reth) / (wsteth_v / wsteth)
        actual_ratio = solidity.to_float(rp.call("curvePool.get_dy", 0, 1, w3.toWei(1, "ether")))
        premium = (actual_ratio / expected_ratio) - 1
        e.add_field(
            name="Current rETH => wstETH Premium",
            value=f"`{premium:.2%}`",
            inline=False,
        )
        # wstETH => rETH premium
        expected_ratio = (wsteth_v / wsteth) / (reth_v / reth)
        actual_ratio = solidity.to_float(rp.call("curvePool.get_dy", 1, 0, w3.toWei(1, "ether")))
        premium = (actual_ratio / expected_ratio) - 1
        e.add_field(
            name="Current wstETH => rETH Premium",
            value=f"`{premium:.2%}`",
            inline=False,
        )
        token_name = rp.call("curvePool.symbol")
        link = el_explorer_url(rp.get_address_by_name("curvePool"), token_name)
        e.add_field(
            name="Contract Address",
            value=link,
        )
        await ctx.send(embed=e)

    @hybrid_command()
    async def yearn(self, ctx: Context):
        """
        Show stats of the yearn vault
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
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
        link = el_explorer_url(rp.get_address_by_name("yearnPool"), token_name)
        e.add_field(
            name="Contract Address",
            value=link,
        )
        await ctx.send(embed=e)

    @hybrid_command()
    async def liquidity(self, ctx: Context):
        """
        Show the RPL liquidity on uniswap v3
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        url = "https://rocketscan.io/api/mainnet/uniswap/rpl"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        total_rpl_liquidity = 0
        for d in data:
            # if both tokens are RPL, skip
            if "RPL" in d["token0"]["symbol"] and "RPL" in d["token1"]["symbol"]:
                continue
            for t in ["token0", "token1"]:
                if "RPL" not in d[t]["symbol"]:
                    continue
                total_rpl_liquidity += solidity.to_float(d[t]["liquidity"])

        e = Embed()
        e.title = "Uniswap v3 Liquidity"
        e.set_author(name="🔗 Data from rocketscan.io", url="https://rocketscan.io/rpl/uniswap")
        if not total_rpl_liquidity:
            e.description = "<@806275470140244019> broke rocketscan.io's API. Please try again later."
        e.add_field(
            name="RPL Liquidity",
            value=f"`{total_rpl_liquidity:,.2f} RPL`",
        )
        await ctx.send(embed=e, allowed_mentions=True)


async def setup(bot):
    await bot.add_cog(DeFi(bot))
