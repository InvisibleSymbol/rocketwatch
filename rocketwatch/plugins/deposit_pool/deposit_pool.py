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

    current_node_demand = solidity.to_float(rp.call("rocketNetworkFees.getNodeDemand"))

    if deposit_cap - deposit_pool < 0.01:
        e.add_field(name="Status:", value="Deposit Pool Cap Reached!", inline=False)
    else:
        percentage_filled = round(deposit_pool / deposit_cap * 100, 2)
        free_capacity = deposit_cap - deposit_pool
        if current_node_demand <= 0:
            free_capacity += current_node_demand * -1
        free_capacity = round(free_capacity, 3)
        e.add_field(name="Status:",
                    value=f"Buffer {percentage_filled}% Full. Enough space for {humanize.intcomma(free_capacity)} more ETH",
                    inline=False)

    current_commission = solidity.to_float(rp.call("rocketNetworkFees.getNodeFee")) * 100
    e.add_field(name="Commission Rate:", value=f"{round(current_commission, 2)}%")

    queue_length = rp.call("rocketMinipoolQueue.getTotalLength")
    e.add_field(name="Current Queue:", value=f"{humanize.intcomma(queue_length)} Minipools")
    e.add_field(name="Enough For:",
                value=f"**`{deposit_pool // 16:>4.0f}`** 16 ETH Minipools" +
                      (f"\n**`{deposit_pool // 24:>4.0f}`** 8 ETH Minipools" if deposit_pool > 24 else "") +
                      (f"\n**`{deposit_pool // 32:>4.0f}`** Credit Minipools" if deposit_pool > 32 else ""),
                inline=False)

    img = BytesIO()
    if rendered_graph := get_graph(img, current_commission, current_node_demand):
        e.set_image(url="attachment://graph.png")
        return e, img
    else:
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
