
import logging

from io import BytesIO
import matplotlib.pyplot as plt
import numpy as np

from discord import Embed, Color, File
from discord.commands import slash_command
from discord.ext import commands

from utils import solidity
from utils.rocketpool import rp
from utils.cfg import cfg
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("rpl_apr")
log.setLevel(cfg["log_level"])


class RplApr(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def rpl_apr(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))

        reward_duration = rp.call("rocketRewardsPool.getClaimIntervalTime")
        total_rpl_staked = solidity.to_float(
            rp.call("rocketNetworkPrices.getEffectiveRPLStake"))
        node_operator_rewards = solidity.to_float(
            rp.call("rocketRewardsPool.getClaimingContractAllowance", "rocketClaimNode"))

        xmin = total_rpl_staked * 0.66
        xmax = total_rpl_staked * 1.33
        x = np.linspace(xmin, xmax)

        def apr_curve(staked):
            return (node_operator_rewards / staked) / (reward_duration / 60 / 60 / 24) * 365
        apr = apr_curve(total_rpl_staked)
        y = apr_curve(x)
        fig = plt.figure()
        plt.plot(x, y, color=str(self.color))
        plt.xlim(xmin, xmax)
        plt.ylim(apr_curve(xmax) * 0.9, apr_curve(xmin) * 1.1)
        plt.plot(total_rpl_staked, apr, 'bo')
        plt.annotate(f"{apr:.2%}", (total_rpl_staked, apr),
                     textcoords="offset points", xytext=(-10, -5), ha='right')
        plt.annotate(f"{total_rpl_staked / 1000000:.2f} million staked",
                     (total_rpl_staked, apr), textcoords="offset points", xytext=(10, -5), ha='left')
        plt.grid()

        ax = plt.gca()
        ax.xaxis.set_major_formatter(lambda x, _: "{:.1f}m".format(x / 1000000))
        ax.yaxis.set_major_formatter("{x:.2%}")
        ax.set_ylabel("APR")
        ax.set_xlabel("RPL Staked")

        img = BytesIO()
        fig.tight_layout()
        fig.savefig(img, format='png')
        img.seek(0)
        fig.clf()
        plt.close()

        e = Embed(color=self.color)
        e.title = "RPL APR Graph"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        await ctx.respond(embed=e, file=f, ephemeral=is_hidden(ctx))
        img.close()


def setup(bot):
    bot.add_cog(RplApr(bot))
