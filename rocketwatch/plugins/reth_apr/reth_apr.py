import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from io import BytesIO

import matplotlib.pyplot as plt
from discord import File
from discord.ext import commands, tasks
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from matplotlib.dates import DateFormatter
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.shared_w3 import w3, historical_w3
from utils.visibility import is_hidden

log = logging.getLogger("reth_apr")
log.setLevel(cfg["log_level"])


def to_apr(d1, d2, effective=True):
    duration = get_duration(d1, d2)
    period_change = get_period_change(d1, d2, effective)
    return period_change * (Decimal(365 * 24 * 60 * 60) / Decimal(duration))


def get_period_change(d1, d2, effective=True):
    v = (Decimal(d2["value"]) - Decimal(d1["value"])) / Decimal(d1["value"])
    if not effective:
        v *= Decimal(1 / d2["effectiveness"])
    return v


def get_duration(d1, d2):
    return d2["time"] - d1["time"]


class RETHAPR(commands.Cog):
    def __init__(self, bot: RocketWatch):
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
            await self.bot.report_error(err)

    def get_time_of_block(self, block_number):
        block = w3.eth.getBlock(block_number)
        return datetime.fromtimestamp(block["timestamp"])

    async def gather_new_data(self):
        # get latest block update from the db
        latest_db_block = await self.db.reth_apr.find_one(sort=[("block", -1)])
        latest_db_block = 0 if latest_db_block is None else latest_db_block["block"]
        cursor_block = historical_w3.eth.getBlock("latest")["number"]
        while True:
            # get address of rocketNetworkBalances contract at cursor block
            address = rp.uncached_get_address_by_name("rocketNetworkBalances", block=cursor_block)
            balance_block = rp.call("rocketNetworkBalances.getBalancesBlock", block=cursor_block, address=address)
            if balance_block == latest_db_block:
                break
            block_time = w3.eth.getBlock(balance_block)["timestamp"]
            # abort if the blocktime is older than 120 days
            if block_time < (datetime.now().timestamp() - 120 * 24 * 60 * 60):
                break
            reth_ratio = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate", block=cursor_block))
            effectiveness = solidity.to_float(
                rp.call("rocketNetworkBalances.getETHUtilizationRate", block=cursor_block, address=address))
            await self.db.reth_apr.insert_one({
                "block"        : balance_block,
                "time"         : block_time,
                "value"        : reth_ratio,
                "effectiveness": effectiveness
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
        datapoints = await self.db.reth_apr.find().sort("block", -1).limit(180 + 38).to_list(None)
        if len(datapoints) == 0:
            e.description = "No data available yet."
            return await ctx.send(embed=e)

            # get average meta.NodeFee from db, weighted by meta.NodeOperatorShare
        tmp = await self.db.minipools_new.aggregate([
            {
                '$match': {
                    'beacon.status'       : 'active_ongoing',
                    'node_fee'            : {
                        '$ne': None
                    },
                    'node_deposit_balance': {
                        '$ne': None
                    }
                }
            }, {
                '$project': {
                    'fee'  : '$node_fee',
                    'share': {
                        '$multiply': [
                            {
                                '$subtract': [
                                    1, {
                                        '$divide': [
                                            '$node_deposit_balance', 32
                                        ]
                                    }
                                ]
                            }, 100
                        ]
                    }
                }
            }, {
                '$group': {
                    '_id'          : None,
                    'pre_numerator': {
                        '$sum': '$fee'
                    },
                    'numerator'    : {
                        '$sum': {
                            '$multiply': [
                                '$fee', '$share'
                            ]
                        }
                    },
                    'denominator'  : {
                        '$sum': '$share'
                    },
                    'count'        : {
                        '$sum': 1
                    }
                }
            }, {
                '$project': {
                    'average'          : {
                        '$divide': [
                            '$numerator', '$denominator'
                        ]
                    },
                    'reference_average': {
                        '$divide': [
                            '$pre_numerator', '$count'
                        ]
                    },
                    'used_pETH_share'  : {
                        '$divide': [
                            {
                                '$divide': [
                                    '$denominator', '$count'
                                ]
                            }, 100
                        ]
                    }
                }
            }
        ]).to_list(length=1)

        node_fee = tmp[0]["average"] if len(tmp) > 0 else 20
        peth_share = tmp[0]["used_pETH_share"] if len(tmp) > 0 else 0.75

        datapoints = sorted(datapoints, key=lambda x: x["time"])
        x = []
        y = []
        y_effectiveness = []
        y_virtual = []
        y_7d = []
        y_7d_claim = None
        y_7d_virtual = []
        for i in range(1, len(datapoints)):
            # add the data of the datapoint to the x values, need to parse it to a datetime object
            x.append(datetime.fromtimestamp(datapoints[i]["time"]))

            # add the average APR to the y values
            y.append(to_apr(datapoints[i - 1], datapoints[i]))
            y_virtual.append(to_apr(datapoints[i - 1], datapoints[i], effective=False))

            y_effectiveness.append(datapoints[i]["effectiveness"])

            # calculate the 7 day average
            if i > 8:
                y_7d.append(to_apr(datapoints[i - 9], datapoints[i]))
                y_7d_virtual.append(to_apr(datapoints[i - 9], datapoints[i], effective=False))
                y_7d_claim = get_duration(datapoints[i - 9], datapoints[i]) / (60 * 60 * 24)
            else:
                # if we dont have enough data, we dont show it
                y_7d.append(None)
                y_7d_virtual.append(None)
        e.add_field(name=f"{y_7d_claim:.1f} Day Average rETH APR",
                    value=f"{y_7d[-1]:.2%}")
        e.add_field(name=f"{y_7d_claim:.1f} Day Average rETH APR (without Effectiveness Drag, Virtual)",
                    value=f"{y_7d_virtual[-1]:.2%}", inline=False)
        fig = plt.figure()
        ax1 = plt.gca()
        ax2 = plt.twinx()

        ax2.plot(x, y, marker="+", linestyle="", label="Period Average", alpha=0.6, color="orange")
        # ax2.plot(x, y_virtual, marker="x", linestyle="", label="Period Average (Virtual)", alpha=0.4)
        # ax2.plot(x, y_node_operators, marker="+", linestyle="", label="Node Operator APR", alpha=0.4)
        ax2.plot(x, y_7d, linestyle="-", label=f"{y_7d_claim:.1f} Day Average", color="orange")
        ax2.plot(x, y_7d_virtual, linestyle="-", label=f"{y_7d_claim:.1f} Day Average (Virtual)", color="green")
        ax1.plot(x, y_effectiveness, linestyle="--", label="Effectiveness", alpha=0.7, color="royalblue")

        plt.title("Observed rETH APR values")
        plt.xlabel("Date")
        plt.grid(True)
        plt.xlim(left=x[38])
        plt.xticks(rotation=45)
        old_formatter = plt.gca().xaxis.get_major_formatter()
        plt.gca().xaxis.set_major_formatter(DateFormatter("%b %d"))

        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:.1%}".format(x)))
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:.1%}".format(x)))
        ax1.set_ylabel("Effectiveness")
        ax2.set_ylabel("APR")
        ax1.set_ylim(top=1)
        ax1.legend(loc="upper left")
        ax2.legend(loc="upper right")

        img = BytesIO()
        fig.tight_layout()
        fig.savefig(img, format='png')
        img.seek(0)
        fig.clf()
        plt.close()

        # reset the x axis formatter
        plt.gca().xaxis.set_major_formatter(old_formatter)

        e.set_image(url="attachment://reth_apr.png")

        e.add_field(name="Current Average Effective Commission:",
                    value=f"{node_fee:.2%} (Observed pETH Share: {peth_share:.2%})",
                    inline=False)

        e.add_field(name="Effectiveness:",
                    value=f"{y_effectiveness[-1]:.2%}",
                    inline=False)
        await ctx.send(embed=e, file=File(img, "reth_apr.png"))

    @hybrid_command()
    async def current_no_apr(self, ctx: Context):
        """
        Show the current NO APR.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        e.title = "Current NO APR"
        e.description = "Dashed red lines above and bellow the solid red one are leb8 and leb16 respectively. " \
                        "The solid line is the protocol average."

        # get the last 30 datapoints
        datapoints = await self.db.reth_apr.find().sort("block", -1).limit(180 + 38).to_list(None)
        if len(datapoints) == 0:
            e.description = "No data available yet."
            return await ctx.send(embed=e)

            # get average meta.NodeFee from db, weighted by meta.NodeOperatorShare
        tmp = await self.db.minipools_new.aggregate([
            {
                '$match': {
                    'beacon.status'       : 'active_ongoing',
                    'node_fee'            : {
                        '$ne': None
                    },
                    'node_deposit_balance': {
                        '$ne': None
                    }
                }
            }, {
                '$project': {
                    'fee'  : '$node_fee',
                    'share': {
                        '$multiply': [
                            {
                                '$subtract': [
                                    1, {
                                        '$divide': [
                                            '$node_deposit_balance', 32
                                        ]
                                    }
                                ]
                            }, 100
                        ]
                    }
                }
            }, {
                '$group': {
                    '_id'          : None,
                    'pre_numerator': {
                        '$sum': '$fee'
                    },
                    'numerator'    : {
                        '$sum': {
                            '$multiply': [
                                '$fee', '$share'
                            ]
                        }
                    },
                    'denominator'  : {
                        '$sum': '$share'
                    },
                    'count'        : {
                        '$sum': 1
                    }
                }
            }, {
                '$project': {
                    'average'          : {
                        '$divide': [
                            '$numerator', '$denominator'
                        ]
                    },
                    'reference_average': {
                        '$divide': [
                            '$pre_numerator', '$count'
                        ]
                    },
                    'used_pETH_share'  : {
                        '$divide': [
                            {
                                '$divide': [
                                    '$denominator', '$count'
                                ]
                            }, 100
                        ]
                    }
                }
            }
        ]).to_list(length=1)

        node_fee = tmp[0]["average"] if len(tmp) > 0 else 0.2
        peth_share = tmp[0]["used_pETH_share"] if len(tmp) > 0 else 0.75

        datapoints = sorted(datapoints, key=lambda x: x["time"])
        x = []
        y_7d = []
        y_7d_claim = None
        y_7d_virtual = []
        y_7d_node_operators_leb8_14 = []
        y_7d_node_operators_leb16_05 = []
        y_7d_node_operators_leb16_14 = []
        y_7d_node_operators_leb16_20 = []
        y_7d_solo = []
        for i in range(1, len(datapoints)):
            # add the data of the datapoint to the x values, need to parse it to a datetime object
            x.append(datetime.fromtimestamp(datapoints[i]["time"]))

            # calculate the 7 day average
            if i > 8:
                y_7d.append(to_apr(datapoints[i - 9], datapoints[i]))
                y_7d_virtual.append(to_apr(datapoints[i - 9], datapoints[i], effective=False))
                bare_apr = y_7d_virtual[-1] / Decimal((1 - node_fee))
                y_7d_solo.append(bare_apr)
                peth_share_leb8 = 0.75
                y_7d_node_operators_leb8_14.append(bare_apr * Decimal(1 + (0.14 * peth_share_leb8 / (1 - peth_share_leb8))))
                peth_share_leb16 = 0.5
                y_7d_node_operators_leb16_05.append(bare_apr * Decimal(1 + (0.05 * peth_share_leb16 / (1 - peth_share_leb16))))
                y_7d_node_operators_leb16_14.append(bare_apr * Decimal(1 + (0.14 * peth_share_leb16 / (1 - peth_share_leb16))))
                y_7d_node_operators_leb16_20.append(bare_apr * Decimal(1 + (0.20 * peth_share_leb16 / (1 - peth_share_leb16))))
                y_7d_claim = get_duration(datapoints[i - 9], datapoints[i]) / (60 * 60 * 24)
            else:
                # if we dont have enough data, we dont show it
                y_7d_solo.append(None)
                y_7d_node_operators_leb8_14.append(None)
                y_7d_node_operators_leb16_05.append(None)
                y_7d_node_operators_leb16_14.append(None)
                y_7d_node_operators_leb16_20.append(None)
        e.add_field(name=f"{y_7d_claim:.1f} Day Average Node Operator APR:",
                    value=f"**leb8:** `{y_7d_node_operators_leb8_14[-1]:.2%}`\n"
                          f"**leb16 5%:** `{y_7d_node_operators_leb16_05[-1]:.2%}` | "
                          f"**leb16 14%:** `{y_7d_node_operators_leb16_14[-1]:.2%}` | "
                          f"**leb16 20%:** `{y_7d_node_operators_leb16_20[-1]:.2%}`", inline=False)

        fig = plt.figure()
        ax1 = plt.gca()

        # solo apr
        ax1.plot(x, y_7d_node_operators_leb8_14, linestyle="-.", label=f"{y_7d_claim:.1f} Day Average (leb8 14%)", color="red", alpha=0.5)
        # use area to show region between leb16 20% and leb16 5%. use a spare dotted fill to show the region between
        ax1.fill_between(x, y_7d_node_operators_leb16_20, y_7d_node_operators_leb16_05, alpha=0.2, color="red", label=f"{y_7d_claim:.1f} Day Average (leb16 5-20%)")
        # plot the leb16 14% line
        ax1.plot(x, y_7d_node_operators_leb16_14, linestyle="--", label=f"{y_7d_claim:.1f} Day Average (leb16 14%)", color="red", alpha=0.5)
        ax1.plot(x, y_7d_solo, linestyle=":", label=f"{y_7d_claim:.1f} Day Average (solo)", color="black", alpha=0.5)

        plt.title("Observed NO APR values")
        plt.grid(True)
        plt.xlim(left=x[38])
        plt.xticks(rotation=0)
        plt.ylim(bottom=0.02)
        old_formatter = plt.gca().xaxis.get_major_formatter()
        plt.gca().xaxis.set_major_formatter(DateFormatter("%m.%d"))

        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:.1%}".format(x)))
        ax1.legend(loc="lower left")

        img = BytesIO()
        fig.tight_layout()
        fig.savefig(img, format='png')
        img.seek(0)
        fig.clf()
        plt.close()

        # reset the x axis formatter
        plt.gca().xaxis.set_major_formatter(old_formatter)

        e.add_field(name="Current Average Effective Commission:",
                    value=f"{node_fee:.2%} (Observed pETH Share: {peth_share:.2%})",
                    inline=False)

        e.set_image(url="attachment://no_apr.png")

        await ctx.send(embed=e, file=File(img, "no_apr.png"))

async def setup(bot):
    await bot.add_cog(RETHAPR(bot))
