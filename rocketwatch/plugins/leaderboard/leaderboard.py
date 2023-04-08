import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import cronitor
import inflect
import pymongo
from discord.ext import commands, tasks
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import Embed
from utils.reporter import report_error
from utils.shared_w3 import bacon
from utils.solidity import to_float
from utils.time_debug import timerun
from utils.visibility import is_hidden

log = logging.getLogger("leaderboard")
log.setLevel(cfg["log_level"])
p = inflect.engine()

cronitor.api_key = cfg["cronitor_secret"]
monitor = cronitor.Monitor('generate-leaderboard')


class Leaderboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.sync_db = pymongo.MongoClient(cfg["mongodb_uri"]).get_database("rocketwatch")

        if not self.run_loop.is_running() and bot.is_ready():
            self.run_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.run_loop.is_running():
            return
        self.run_loop.start()

    @timerun
    def get_balances(self, slot):
        log.debug(f"Getting balances for slot {slot}")
        return bacon.get_validator_balances(slot)["data"]

    @tasks.loop(seconds=60 ** 2)
    async def run_loop(self):
        p_id = time.time()
        monitor.ping(state='run', series=p_id)
        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()
        futures = [loop.run_in_executor(executor, self.cache_embed)]
        try:
            await asyncio.gather(*futures)
            monitor.ping(state='complete', series=p_id)
        except Exception as err:
            await report_error(err)
            monitor.ping(state='fail', series=p_id)

    def cache_embed(self):
        # get current slot
        current = int(bacon.get_block("head")["data"]["message"]["slot"])
        current_epoch = current // 32
        epochs_per_day = (60 / 12) / 32 * 60 * 24
        # get balances now
        current_balances = self.get_balances(slot=current)
        # get balances a week ago
        last_week = current - int(60 / 12 * 60 * 24 * 7)
        last_week_balances = self.get_balances(last_week)
        # get all validators from db
        validators = list(
            self.sync_db.minipools.find(
                {"activation_epoch": {"$lte": last_week / 32}},
                {"validator": 1, "activation_epoch": 1}
            )
        )
        activation_epochs = {
            validator["validator"]: validator["activation_epoch"]
            for validator in validators
        }
        validators = [x["validator"] for x in validators]
        validator_data = {}
        # update balances of validators
        batch = []
        cvb = {int(v["index"]): to_float(v["balance"], 9) for v in current_balances if int(v["index"]) in validators}
        for v, b in cvb.items():
            if b == 16:
                continue
            validator_data[v] = {"current_balance": b}
            batch.append(
                pymongo.UpdateOne(
                    {"validator": v},
                    {"$set": {"balance": b}}
                )
            )
        self.sync_db.minipools.bulk_write(batch)
        # filter
        last_week_data = [v for v in last_week_balances if int(v["index"]) in validators]
        for v in last_week_data:
            index = int(v["index"])
            # split for performance reasons
            balance = to_float(v["balance"], 9)
            days_active = (current_epoch - activation_epochs[index]) / epochs_per_day
            if balance <= 16 or days_active < 7:
                continue
            validator_data[index]["last_week_balance"] = balance
            validator_data[index]["days_active"] = days_active

        # generate new dictonary with validator index as key and current and last week balances as values
        balances = {
            i: {
                "current"       : vd["current_balance"],
                "last_week"     : vd["last_week_balance"],
                "daily_earnings": (vd["current_balance"] - 32) / vd["days_active"]
            } for i, vd in validator_data.items()
        }
        # calculate APR attribute
        for i in balances:
            # get percentage change between first and last datapoint
            total_change_percent = (balances[i]["current"] - balances[i]["last_week"]) / balances[i]["last_week"]

            # extrapolate change to 1 year
            yearly_change = total_change_percent * 52
            balances[i]["apr"] = yearly_change * 100

        # generate 7 day leaderboard embed

        # get sorted list of validators
        sorted_validators = sorted(balances.items(), key=lambda x: x[1]["apr"], reverse=True)

        # generate embed
        e = Embed(
            title="APR Leaderboard (last 7 days)",
        )
        e.set_footer(text=f"Last updated Slot {current}")

        # add top 10 validators
        desc = "```\n"
        for i, (validator, data) in enumerate(sorted_validators[:5]):
            desc += f"\n{f'#{i + 1}':>5}: {validator} - {data['current']:.2f}ETH (APR: {data['apr']:>5.2f}%)"
        desc += f"\n{'...':>5}"
        for i, (validator, data) in enumerate(sorted_validators[-30:]):
            i = len(sorted_validators) - 30 + i
            desc += f"\n{f'#{i}':>5}: {validator} - {data['current']:.2f}ETH (APR: {data['apr']:>5.2f}%)"
        desc += "\n```"
        e.description = desc

        self.sync_db.leaderboard.update_one(
            {"_id": "leaderboard_7days"},
            {"$set": {"embed": e.to_dict()}},
            upsert=True
        )

        # generate daily earning leaderboard embed

        # get sorted list of validators
        sorted_validators = sorted(balances.items(), key=lambda x: x[1]["daily_earnings"], reverse=True)

        # generate embed
        e = Embed(
            title="Daily Earnings Leaderboard (All time)",
        )
        e.set_footer(text=f"Last updated Slot {current}")

        # add top 10 validators
        desc = "```\n"
        for i, (validator, data) in enumerate(sorted_validators[:5]):
            desc += f"\n{f'#{i + 1}':>5}: {validator} - {data['current']:.2f}ETH ({data['daily_earnings']:.5f} ETH/day)"
        desc += f"\n{'...':>5}"
        for i, (validator, data) in enumerate(sorted_validators[-30:]):
            i = len(sorted_validators) - 30 + i
            desc += f"\n{f'#{i}':>5}: {validator} - {data['current']:.2f}ETH ({data['daily_earnings']:.5f} ETH/day)"
        desc += "\n```"
        e.description = desc

        self.sync_db.leaderboard.update_one(
            {"_id": "leaderboard_daily_earnings"},
            {"$set": {"embed": e.to_dict()}},
            upsert=True
        )

        log.debug(f"Cached embeds for slot {current}")

    @hybrid_command()
    async def leaderboard(self,
                          ctx: Context,
                          all_time: bool = False):
        """
        Generate leaderboard for minipool performance
        """
        await ctx.defer(ephemeral=is_hidden(ctx))

        # get embed from db
        target = "leaderboard_7days" if not all_time else "leaderboard_daily_earnings"
        embed_dict = await self.db.leaderboard.find_one({"_id": target})
        if embed_dict is None:
            await ctx.send(
                content="Leaderboard is not cached yet. Please wait a minute and try again.")
            return

        # generate embed from dict
        e = Embed.from_dict(embed_dict["embed"])
        await ctx.send(embed=e)

    @hybrid_command()
    async def minipool_balance_stats(self, ctx: Context):
        """
        Get average, median, min and max minipool balance
        """
        await ctx.defer(ephemeral=is_hidden(ctx))

        # get minipool balances
        balances = await self.db.minipools.find(
            {"balance": {"$gt": 0}},
            {"balance": 1, "_id": 0}
        ).to_list(None)

        # get average, median, min and max
        average = sum(b["balance"] for b in balances) / len(balances)
        median = sorted([b["balance"] for b in balances])[len(balances) // 2]
        min_balance = min(b["balance"] for b in balances)
        max_balance = max(b["balance"] for b in balances)

        # generate embed
        e = Embed(
            title="Minipool Balance Stats",
            description=f"Average: {average:.2f} ETH\nMedian: {median:.2f} ETH\nMin: {min_balance:.2f} ETH\nMax: {max_balance:.2f} ETH"
        )
        await ctx.send(embed=e)

    # calculate amount of ETH that will be withdrawn on the first withdrawal check (that is, anything above 32 ETH)
    # note: validators can have balances under 32, in that case they will be ignored
    @hybrid_command()
    async def minipool_withdrawal_stats(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden(ctx))

        # get minipool balances
        balances = await self.db.minipools.find(
            {"balance": {"$gt": 32}},
            {"balance": 1, "_id": 0}
        ).to_list(None)

        # get amount over 32
        amount_over_32 = sum(b["balance"] - 32 for b in balances)

        # generate embed
        e = Embed(
            title="Minipool Withdrawal Stats",
            description=f"Amount over 32 ETH: {amount_over_32:.2f} ETH"
        )
        await ctx.send(embed=e)



async def setup(bot):
    await bot.add_cog(Leaderboard(bot))
