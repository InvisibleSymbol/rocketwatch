import logging

import humanize
from discord import Embed, Color
from discord.commands import slash_command
from discord.ext import commands

from utils import solidity
from utils.cfg import cfg
from utils.rocketpool import rp
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("effective_rpl")
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
        # get total supply
        total_rpl_supply = solidity.to_float(rp.call("rocketTokenRPL.totalSupply"))
        # calculate total staked as a percentage of total supply
        percentage_of_total_staked = total_rpl_staked / total_rpl_supply
        e.add_field(name="Percentage of RPL Supply Staked:", value=f"{percentage_of_total_staked:.2%}", inline=False)
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(EffectiveRPL(bot))
