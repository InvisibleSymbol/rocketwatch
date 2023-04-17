import contextlib
import logging
from io import BytesIO

import aiohttp
import humanize
import matplotlib.pyplot as plt
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.readable import s_hex
from utils.rocketpool import rp
from utils.thegraph import get_uniswap_pool_depth, get_uniswap_pool_stats
from utils.visibility import is_hidden_weak

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
            # fallback to alternative method
            try:
                alternative = self._get_alternative_wall()
                e.set_image(url="attachment://wall.png")
                await ctx.send(
                    embed=e,
                    file=File(
                        alternative, filename="wall.png"
                    ))
                return
            except Exception as err:
                log.exception(err)
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

    def _get_alternative_wall(self):
        # test the get_uniswap_pool_depth function
        a = get_uniswap_pool_depth("0xe42318ea3b998e8355a3da364eb9d48ec725eb45")
        # get current price from the pool stats
        sqrt_price = get_uniswap_pool_stats("0xe42318ea3b998e8355a3da364eb9d48ec725eb45")["sqrtPrice"]
        price = 1 / (int(sqrt_price) ** 2 / 2 ** 192)

        plt.plot([x[0] for x in a], [x[1] for x in a], drawstyle="steps-pre", color="black", linewidth=1)

        # color everything above the current tick red, below green
        # get the closest tick to the current price
        idx = min(range(len(a)), key=lambda i: abs(a[i][0] - price))
        above = a[idx:]
        below = a[:idx + 1]
        # plot the two lists
        plt.fill_between([x[0] for x in above], [x[1] for x in above], color="red", alpha=0.5, interpolate=False, step="pre")
        plt.fill_between([x[0] for x in below], [x[1] for x in below], color="green", alpha=0.5, interpolate=False, step="pre")
        # plot the current price as a vertical line
        plt.axvline(price, color="black", linestyle="--", linewidth=1)

        # hide y axis
        plt.gca().axes.get_yaxis().set_visible(False)

        # minor ticks for the x axis
        plt.gca().xaxis.set_minor_locator(plt.MultipleLocator(0.001))

        # set x axis ticks to numbers
        plt.gca().xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.3f}"))

        # set y axis min to 0
        plt.ylim(bottom=0)

        # vertical grid lines
        plt.gca().xaxis.grid(True, which="major", linestyle="--")
        plt.gca().xaxis.grid(True, which="minor", linestyle=":")

        # center the plot around the current price, make the x axis 4x as wide
        plt.xlim(price * 0.25, price * 1.75)

        # use minimal whitespace
        plt.tight_layout()

        # store the graph in an file object
        file = BytesIO()
        # make sure to increase the dpi to make the graph look better
        plt.savefig(file, format='png', dpi=300)
        file.seek(0)

        # clear plot from memory
        plt.clf()
        plt.close()

        return file


async def setup(bot):
    await bot.add_cog(Wall(bot))
