import logging
from io import BytesIO

import humanize
from discord import Embed, Color
from discord import File
from discord.commands import slash_command
from discord.ext import commands

from utils import solidity
from utils.cfg import cfg
from utils.deposit_pool_graph import get_graph
from utils.rocketpool import rp
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("despoit_pool")
log.setLevel(cfg["log_level"])


class DepositPool(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def dp(self, ctx):
        """Deposit Pool Stats"""
        await self._dp(ctx)

    @slash_command(guild_ids=guilds)
    async def deposit_pool(self, ctx):
        """Deposit Pool Stats"""
        await self._dp(ctx)

    async def _dp(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(colour=self.color)
        e.title = "Deposit Pool Stats"

        deposit_pool = solidity.to_float(rp.call("rocketDepositPool.getBalance"))
        e.add_field(name="Current Size:", value=f"{humanize.intcomma(round(deposit_pool, 3))} ETH")

        deposit_cap = solidity.to_int(rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositPoolSize"))
        e.add_field(name="Maximum Size:", value=f"{humanize.intcomma(deposit_cap)} ETH")

        current_node_demand = solidity.to_float(rp.call("rocketNetworkFees.getNodeDemand"))

        if deposit_cap - deposit_pool < 0.01:
            e.add_field(name="Status:",
                        value=f"Deposit Pool Cap Reached!",
                        inline=False)
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
        e.add_field(name="Current Commission Rate:", value=f"{round(current_commission, 2)}%", inline=False)

        minipool_count = int(deposit_pool / 16)
        e.add_field(name="Enough For:", value=f"{minipool_count} new Minipools")

        queue_length = rp.call("rocketMinipoolQueue.getTotalLength")
        e.add_field(name="Current Queue:", value=f"{humanize.intcomma(queue_length)} Minipools")

        img = BytesIO()
        rendered_graph = get_graph(img, current_commission, current_node_demand)
        if rendered_graph:
            e.set_image(url="attachment://graph.png")
            f = File(img, filename="graph.png")
            await ctx.respond(embed=e, file=f, ephemeral=is_hidden(ctx))
        else:
            await ctx.respond(embed=e, ephemeral=is_hidden(ctx))
        img.close()


def setup(bot):
    bot.add_cog(DepositPool(bot))
