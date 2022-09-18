import logging
from datetime import datetime
from io import BytesIO

from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from matplotlib.dates import DateFormatter
from motor.motor_asyncio import AsyncIOMotorClient
import matplotlib.pyplot as plt

from utils.cfg import cfg
from utils.embeds import Embed
from utils.readable import uptime
from utils.thegraph import get_reth_ratio_past_month
from utils.visibility import is_hidden

log = logging.getLogger("reth_apr")
log.setLevel(cfg["log_level"])


class RETHAPR(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @hybrid_command()
    async def current_reth_apr(self, ctx: Context):
        """
        Show the current rETH APR.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()

        datapoints = get_reth_ratio_past_month()
        # datapoints = datapoints[-3:]

        # get total duration between first and last datapoint
        week_duration = datapoints[-1]["time"] - datapoints[0]["time"]

        # get change between first and last datapoint
        period_change_week = datapoints[-1]["value"] - datapoints[-8]["value"]
        yearly_change_week = (period_change_week / week_duration) * 365 * 24 * 60 * 60
        percentage_change_yearly_using_week = ((datapoints[-1]["value"] + yearly_change_week) / datapoints[-1]["value"]) - 1

        e.add_field(name="Observed rETH APR (7 day average):",
                    value=f"{percentage_change_yearly_using_week:.2%} (Commissions Fees accounted for)",
                    inline=False)

        # we loop through pairs of datapoints[n-1], datapoints[n] to get the average APR for each day
        # we use this to generate x and y values for a line graph
        x = []
        y = []
        # we also calculate a running average of 7 days. if we dont have enough data, we dont show it
        y_7d = []
        for i in range(1, len(datapoints)):
            # get the duration between the two datapoints
            duration = datapoints[i]["time"] - datapoints[i - 1]["time"]

            # get the change between the two datapoints
            period_change = datapoints[i]["value"] - datapoints[i - 1]["value"]
            period_change_over_year = (period_change / duration) * 365 * 24 * 60 * 60

            # get the average APR for the day
            average_apr = ((datapoints[i]["value"] + period_change_over_year) / datapoints[i]["value"]) - 1

            # add the average APR to the y values
            y.append(average_apr)

            # add the data of the datapoint to the x values, need to parse it to a datetime object
            x.append(datetime.fromtimestamp(datapoints[i]["time"]))

            if i > 6:
                # calculate the 7 day average
                y_7d.append(sum(y[-7:]) / 7)
            else:
                # if we dont have enough data, we dont show it
                y_7d.append(None)

        fig = plt.figure()
        # format the daily average line as a line with dots
        plt.plot(x, y, color=str(e.color), linestyle="-", marker=".", label="Daily Average")
        # format the 7 day average line as --
        plt.plot(x, y_7d, color=str(e.color), linestyle="--", label="7 Day Average")
        plt.title("Observed rETH APR values")
        plt.xlabel("Date")
        plt.ylabel("APR")
        plt.grid(True)
        # format y axis as percentage
        plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:.0%}".format(x)))
        # set the y axis to start at 0
        plt.ylim(bottom=0)
        # rotate x axis labels
        plt.xticks(rotation=45)
        # show the legend
        plt.legend()
        # dont show year in x axis labels
        old_formatter = plt.gca().xaxis.get_major_formatter()
        plt.gca().xaxis.set_major_formatter(DateFormatter("%b %d"))

        img = BytesIO()
        fig.tight_layout()
        fig.savefig(img, format='png')
        img.seek(0)
        fig.clf()
        plt.close()

        # reset the x axis formatter
        plt.gca().xaxis.set_major_formatter(old_formatter)

        e.set_image(url="attachment://reth_apr.png")
        """
        # get average node_fee from db
        node_fee = await self.db.minipools.aggregate([
            {"$match": {"node_fee": {"$exists": True}}},
            {"$group": {"_id": None, "avg": {"$avg": "$node_fee"}}}
        ]).to_list(length=1)

        e.add_field(name="Current Average Commission:", value=f"{node_fee[0]['avg']:.2%}")
        """

        await ctx.send(embed=e, file=File(img, "reth_apr.png"))


async def setup(bot):
    await bot.add_cog(RETHAPR(bot))
