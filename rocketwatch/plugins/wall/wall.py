import contextlib
import logging
import humanize
import aiohttp

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.visibility import is_hidden_weak
from utils.readable import s_hex

log = logging.getLogger("wall")
log.setLevel(cfg["log_level"])


class Wall(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def wall(self, ctx: Context):
        """
        Show the current limit order sell wall on 1inch
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        wall_address = "0xD779bB0F68F54f7521aA5b35dD88352771843764"
        rpl = rp.get_address_by_name("rocketTokenRPL").lower()
        url = f"https://limit-orders.1inch.io/v3.0/1/limit-order/address/{wall_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        total_volume_left = 0
        total_volume_rpl = 0
        maker_rate_min = 1
        maker_rate_max = 0
        for d in data:
            if d["data"]["makerAsset"] != rpl:
                continue
            total_volume_left += solidity.to_float(d["remainingMakerAmount"])
            total_volume_rpl += solidity.to_float(d["data"]["makingAmount"])
            rate = float(d["makerRate"])
            if rate < maker_rate_min:
                maker_rate_min = rate
            if rate > maker_rate_max:
                maker_rate_max = rate

        e = Embed()
        if total_volume_left == 0:
            e.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
            await ctx.send(embed=e)
            return

        rpl_balance = 0
        with contextlib.suppress(Exception):
            resp = rp.multicall.aggregate(
                rp.get_contract_by_name(name).functions.balanceOf(wall_address)
                for name in ["rocketTokenRPL", "rocketTokenRPLFixedSupply"]
            )
            for token in resp.results:
                contract_name = rp.get_name_by_address(token.contract_address)
                if "RPL" in contract_name:
                    rpl_balance += solidity.to_float(token.results[0])

        e.title = "1inch Sell Wall"
        e.set_author(name="🔗 Data from 1inch.io", url="https://1inch.io/")
        percent = 100 * total_volume_left / total_volume_rpl
        e.add_field(
            name="Liquidity", value=f"{humanize.intcomma(total_volume_left, 0)} RPL"
        )
        e.add_field(
            name="Range", value=f"{maker_rate_min:,.4f} - {maker_rate_max:,.4f}"
        )
        e.add_field(name="Status", value=f"{percent:,.2f}% left", inline=False)
        e.add_field(name="Wallet RPL Balance", value=humanize.intcomma(rpl_balance, 0))
        e.add_field(name="Wallet Address", value=f"[{s_hex(wall_address)}](https://rocketscan.io/address/{wall_address})")
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Wall(bot))
