import logging
from io import BytesIO

import numpy as np
import seaborn as sns
from discord import File
from discord.commands import slash_command
from discord.ext import commands
from matplotlib import pyplot as plt
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import Embed
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("commissions")
log.setLevel(cfg["log_level"])


class Commissions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # connect to local mongodb
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @slash_command(guild_ids=guilds)
    async def commission_history(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))

        e = Embed(title='Commission History')

        minipools = await self.db.minipools.find().sort("validator", 1).to_list(None)
        # create dot chart of minipools
        # x-axis: validator
        # y-axis: node_fee
        ygrid = list(reversed(range(5, 21)))
        step_size = int(len(minipools) / len(ygrid) / 2)

        data = [[0] * len(ygrid)]
        for pool in minipools:
            if sum(data[-1]) > step_size:
                # normalize data
                # data[-1] = [x / max(data[-1]) for x in data[-1]]
                data.append([0] * len(ygrid))
            # round to closet ygrid
            data[-1][ygrid.index(int(round(pool["node_fee"] * 100, 0)))] += 1

        # normalize data
        # data[-1] = [x / max(data[-1]) for x in data[-1]]
        # heatmap distribution over time
        data = np.array(data).T
        ax = sns.heatmap(data, cmap="viridis", yticklabels=ygrid, xticklabels=False)
        ax.set_yticklabels(ax.get_yticklabels(), rotation=0, fontsize=8)
        # set y ticks
        ax.set_ylabel("Node Fee")
        plt.tight_layout()

        # save figure to buffer
        buf = BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)

        # respond with image
        img = BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        e.set_image(url="attachment://chart.png")
        e.add_field(name="Total Minipools", value=len(minipools))
        e.add_field(name="Bar Width", value=f"{step_size} minipools")

        # send data
        await ctx.respond(content="", embed=e, file=File(img, filename="chart.png"))
        img.close()


def setup(bot):
    bot.add_cog(Commissions(bot))
