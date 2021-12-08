import logging

from discord import Embed, Color
from discord.commands import slash_command
from discord.ext import commands

from utils import solidity
from utils.cfg import cfg
from utils.readable import uptime
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.thegraph import get_average_commission, get_reth_ratio_past_week
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
        e.title = "rETH APR over the last 7 days"

        datapoints = get_reth_ratio_past_week()

        # get total duration between first and last datapoint
        total_duration = datapoints[-1]["time"] - datapoints[0]["time"]

        # get average duration between datapoints
        average_duration = total_duration / len(datapoints)

        # next estimated update
        next_update = int(datapoints[-1]["time"] + average_duration)

        # total change
        total_change = datapoints[-1]["value"] - datapoints[0]["value"]

        # get average change per 24h
        average_daily_change = total_change / (total_duration / 86400)

        # turn into yearly percentage
        yearly_change = average_daily_change * 365

        e.description = "**Note**: In the early stages of rETH the calculated APR might be lower than expected!\n" \
                        "This is caused by many things, such as a high stale ETH ratio lowering the earned rewards per ETH" \
                        " or a low Minipool count combined with bad luck simply resulting in temporary lower rewards."

        e.add_field(name="Observed rETH APR:",
                    value=f"{yearly_change:.2%} (Commissions Fees accounted for)",
                    inline=False)

        # get current average commission
        current_commission = get_average_commission()
        e.add_field(name="Current Average Commission:", value=f"{current_commission:.2%}")

        # show next estimated update
        e.add_field(name="Next Estimated Update:", value=f"<t:{next_update}:R>")

        # show average time between updates in footer
        e.set_footer(text=f"Average time between updates: {uptime(average_duration)}. {len(datapoints)} datapoints used.")
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(RETHAPR(bot))
