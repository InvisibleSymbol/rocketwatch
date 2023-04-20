import asyncio
import logging
from datetime import datetime, timedelta
from io import BytesIO

import matplotlib.pyplot as plt
from discord import File
from discord.ext import commands, tasks
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from matplotlib.dates import DateFormatter
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.shared_w3 import w3, historical_w3
from utils.thegraph import get_reth_ratio_past_month
from utils.visibility import is_hidden

log = logging.getLogger("reth_apr")
log.setLevel(cfg["log_level"])


class RETHAPR(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

        if not self.run_loop.is_running() and bot.is_ready():
            self.run_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.run_loop.is_running():
            return
        self.run_loop.start()

    @tasks.loop(seconds=60)
    async def run_loop(self):
        try:
            await self.gather_new_data()
        except Exception as err:
            await report_error(err)

    def get_time_of_block(self, block_number):
        block = w3.eth.getBlock(block_number)
        return datetime.fromtimestamp(block["timestamp"])

    async def gather_new_data(self):
        # get latest block update from the db
        latest_db_block = await self.db.reth_apr.find_one(sort=[("block", -1)])
        latest_db_block = 0 if latest_db_block is None else latest_db_block["block"]
        cursor_block = historical_w3.eth.getBlock("latest")["number"]
        while True:
            balance_block = rp.call("rocketNetworkBalances.getBalancesBlock", block=cursor_block)
            if balance_block == latest_db_block:
                break
            block_time = w3.eth.getBlock(balance_block)["timestamp"]
            # abort if the blocktime is older than 120 days
            if block_time < (datetime.now().timestamp() - 120 * 24 * 60 * 60):
                break
            reth_ratio = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate", block=cursor_block))
            await self.db.reth_apr.insert_one({
                "block": balance_block,
                "time": block_time,
                "value": reth_ratio
            })
            cursor_block = balance_block - 1
            await asyncio.sleep(0.01)

    @hybrid_command()
    async def current_reth_apr(self, ctx: Context):
        """
        Show the current rETH APR.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        e.title = "Current rETH APR"
        e.description = "For some comparisons against other LST: [dune dashboard](https://dune.com/rp_community/lst-comparison)"

        # get the last 30 datapoints
        datapoints = await self.db.reth_apr.find().sort("block", -1).limit(90 +38).to_list(None)
        if len(datapoints) == 0:
            e.description = "No data available yet."
            return await ctx.send(embed=e)
        datapoints = sorted(datapoints, key=lambda x: x["time"])
        x = []
        y = []
        # we do a 7 day rolling average (9 periods) and a 30 day one (38 periods)
        y_7d = []
        y_30d = []
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

            # calculate the 7 day average
            if i > 8:
                y_7d.append(sum(y[-9:]) / 9)
            else:
                # if we dont have enough data, we dont show it
                y_7d.append(None)
            # calculate the 30 day average
            if i > 37:
                y_30d.append(sum(y[-38:]) / 38)
            else:
                # if we dont have enough data, we dont show it
                y_30d.append(None)

        e.add_field(name="7.2 Day Average rETH APR",
                    value=f"{y_7d[-1]:.2%}")
        e.add_field(name="30.4 Day Average rETH APR",
                    value=f"{y_30d[-1]:.2%}")
        fig = plt.figure()
        # format the daily average line as a line with dots
        plt.plot(x, y, marker="+", linestyle="", label="Period Average", alpha=0.7)
        # format the 7 day average line as --
        plt.plot(x, y_7d, linestyle="-", label="7.2 Day Average")
        # format the 30 day average line as --
        plt.plot(x, y_30d, linestyle="-", label="30.4 Day Average")
        plt.title("Observed rETH APR values")
        plt.xlabel("Date")
        plt.ylabel("APR")
        plt.grid(True)
        # format y axis as percentage
        plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:.0%}".format(x)))
        # set the y axis to start at 0
        # plt.ylim(bottom=0)
        # make the x axis skip the first 30 datapoints
        plt.xlim(left=x[38])
        # rotate x axis labels
        plt.xticks(rotation=45)
        # show the legend top left
        plt.legend(loc="upper left")
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
        # get average node_fee from db
        node_fee = await self.db.minipools.aggregate([
            {"$match": {"node_fee": {"$exists": True}}},
            {"$group": {"_id": None, "avg": {"$avg": "$node_fee"}}}
        ]).to_list(length=1)

        e.add_field(name="Current Average Commission:", value=f"{node_fee[0]['avg']:.2%}", inline=False)

        await ctx.send(embed=e, file=File(img, "reth_apr.png"))


async def setup(bot):
    await bot.add_cog(RETHAPR(bot))
