import logging
from io import BytesIO

import humanize
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.deposit_pool_graph import get_graph
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.visibility import is_hidden

log = logging.getLogger("despoit_pool")
log.setLevel(cfg["log_level"])


async def get_dp():
    e = Embed()
    e.title = "Deposit Pool Stats"

    deposit_pool = solidity.to_float(rp.call("rocketDepositPool.getBalance"))
    e.add_field(name="Current Size:", value=f"{humanize.intcomma(round(deposit_pool, 3))} ETH")

    deposit_cap = solidity.to_int(rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositPoolSize"))
    e.add_field(name="Maximum Size:", value=f"{humanize.intcomma(deposit_cap)} ETH")

    if deposit_cap - deposit_pool < 0.01:
        e.add_field(name="Status:", value="Deposit Pool Cap Reached!", inline=False)
    else:
        percentage_filled = round(deposit_pool / deposit_cap * 100, 2)
        free_capacity = solidity.to_float(rp.call("rocketDepositPool.getMaximumDepositAmount"))
        free_capacity = round(free_capacity, 3)
        e.add_field(name="Status:",
                    value=f"Buffer {percentage_filled}% Full. Enough space for {humanize.intcomma(free_capacity)} more ETH",
                    inline=False)

    queue_length = rp.call("rocketMinipoolQueue.getTotalLength")
    if queue_length > 0:
        e.add_field(name="Current Queue:", value=f"{humanize.intcomma(queue_length)} Minipools", inline=False)

    if deposit_pool // 16 > 0:
        e.add_field(name="Enough For:",
                    value=f"**`{deposit_pool // 16:>4.0f}`** 16 ETH Minipools (16 ETH from DP)" +
                          f"\n**`{deposit_pool // 24:>4.0f}`** 8 ETH Minipools (24 ETH from DP)" +
                          f"\n**`{deposit_pool // 32:>4.0f}`** Credit Minipools (32 ETH from DP)",
                    inline=False)

    return e, None


class DepositPool(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def dp(self, ctx: Context):
        """Deposit Pool Stats"""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e, i = await get_dp()
        if i:
            e.set_image(url="attachment://graph.png")
            f = File(i, filename="graph.png")
            await ctx.send(embed=e, files=[f])
            i.close()
        else:
            await ctx.send(embed=e)

    @hybrid_command()
    async def deposit_pool(self, ctx: Context):
        """Deposit Pool Stats"""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e, i = await get_dp()
        if i:
            e.set_image(url="attachment://graph.png")
            f = File(i, filename="graph.png")
            await ctx.send(embed=e, files=[f])
            i.close()
        else:
            await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(DepositPool(bot))
