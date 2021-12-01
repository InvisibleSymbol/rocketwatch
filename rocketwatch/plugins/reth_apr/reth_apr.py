import asyncio
import logging
from datetime import datetime
from io import BytesIO

import humanize
import pytz
from discord import Embed, Color
from discord import File
from discord.ext import commands
from discord.commands import slash_command

from utils import solidity
from utils.cfg import cfg
from utils.deposit_pool_graph import get_graph
from utils.readable import etherscan_url, uptime
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.thegraph import get_average_commission
from utils.visibility import is_hidden

log = logging.getLogger("RETHAPR")
log.setLevel(cfg["log_level"])


class RETHAPR(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def current_reth_apr(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(color=self.color)
        e.title = "Current Estimated rETH APR"

        # get update blocks
        current_update_block = rp.call("rocketNetworkBalances.getBalancesBlock")
        previous_update_block = rp.call("rocketNetworkBalances.getBalancesBlock", block=current_update_block - 1)

        # get timestamps of blocks
        current_update_timestamp = w3.eth.get_block(current_update_block).timestamp
        previous_update_timestamp = w3.eth.get_block(previous_update_block).timestamp

        # average block time
        average_block_time = (current_update_timestamp - previous_update_timestamp) / (current_update_block - previous_update_block)

        # estimate next update timestamp by last 2 updates
        next_update_timestamp = current_update_timestamp + (current_update_timestamp - previous_update_timestamp)

        # get ratios after and before current update block
        current_ratio = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate"))
        previous_ratio = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate", block=current_update_block - 1))

        # calculate the percentage increase in ratio over 24 hours
        ratio_temp = (current_ratio / previous_ratio) - 1
        ratio_increase = ratio_temp * ((24 * 60 * 60) / (current_update_timestamp - previous_update_timestamp))

        # turn into yearly percentage
        yearly_percentage = ratio_increase * 365

        e.description = "**Note**: In the early stages of rETH the calculated APR might be lower than expected!\n" \
                        "This is caused by many things, such as a high stale ETH ratio lowering the earned rewards per ETH" \
                        " or a low Minipool count combined with bad luck simply resulting in lower rewards for a day."

        e.add_field(name="Latest rETH/ETH Updates:",
                    value=f"`{current_ratio:.6f}` on <t:{current_update_timestamp}>\n"
                          f"`{previous_ratio:.6f}` on <t:{previous_update_timestamp}>\n"
                          f"Next Update expected <t:{next_update_timestamp}:R>\n",
                    inline=False)

        # get current average commission
        current_commission = get_average_commission()

        e.add_field(name="Observed rETH APR:", value=f"{yearly_percentage:.2%} (Commission Fee of {current_commission:.2%} taken into account)", inline=False)

        e.set_footer(
            text=f"Duration between used ratio updates: {uptime(current_update_timestamp - previous_update_timestamp)}")

        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))  # respond


def setup(bot):
    bot.add_cog(RETHAPR(bot))
