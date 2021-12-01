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

log = logging.getLogger("EffectiveRPL")
log.setLevel(cfg["log_level"])


class EffectiveRPL(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def effective_rpl_staked(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(color=self.color)
        # get total RPL staked
        total_rpl_staked = solidity.to_float(rp.call("rocketNodeStaking.getTotalRPLStake"))
        e.add_field(name="Total RPL Staked:", value=f"{humanize.intcomma(total_rpl_staked, 2)} RPL", inline=False)
        # get effective RPL staked
        effective_rpl_stake = solidity.to_float(rp.call("rocketNetworkPrices.getEffectiveRPLStake"))
        # calculate percentage staked
        percentage_staked = effective_rpl_stake / total_rpl_staked
        e.add_field(name="Effective RPL Staked:", value=f"{humanize.intcomma(effective_rpl_stake, 2)} RPL "
                                                        f"({percentage_staked:.2%})", inline=False)
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(EffectiveRPL(bot))
