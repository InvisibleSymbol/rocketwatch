import logging

import humanize
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.visibility import is_hidden

log = logging.getLogger("effective_rpl")
log.setLevel(cfg["log_level"])


class EffectiveRPL(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @hybrid_command()
    async def effective_rpl_staked(self, ctx: Context):
        """
        Show the effective RPL staked by users
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        # get total RPL staked
        total_rpl_staked = solidity.to_float(rp.call("rocketNodeStaking.getTotalRPLStake"))
        e.add_field(name="Total RPL Staked:", value=f"{humanize.intcomma(total_rpl_staked, 2)} RPL", inline=False)
        # get effective RPL staked
        effective_rpl_stake = await self.db.node_operators_new.aggregate([
            {
                '$group': {
                    '_id'                      : 'out',
                    'total_effective_rpl_stake': {
                        '$sum': '$effective_rpl_stake'
                    }
                }
            }
        ]).next()
        effective_rpl_stake = effective_rpl_stake["total_effective_rpl_stake"]        # calculate percentage staked
        percentage_staked = effective_rpl_stake / total_rpl_staked
        e.add_field(name="Effective RPL Staked:", value=f"{humanize.intcomma(effective_rpl_stake, 2)} RPL "
                                                        f"({percentage_staked:.2%})", inline=False)
        # get total supply
        total_rpl_supply = solidity.to_float(rp.call("rocketTokenRPL.totalSupply"))
        # calculate total staked as a percentage of total supply
        percentage_of_total_staked = total_rpl_staked / total_rpl_supply
        e.add_field(name="Percentage of RPL Supply Staked:", value=f"{percentage_of_total_staked:.2%}", inline=False)
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(EffectiveRPL(bot))
