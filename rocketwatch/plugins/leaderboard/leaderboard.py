import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import inflect
import pymongo
from discord import Option
from discord.commands import slash_command
from discord.ext import commands, tasks
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import Embed
from utils.reporter import report_error
from utils.shared_w3 import bacon
from utils.slash_permissions import guilds
from utils.solidity import to_float
from utils.visibility import is_hidden

log = logging.getLogger("leaderboard")
log.setLevel(cfg["log_level"])
p = inflect.engine()


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

    def get_balances(self, slot):
        log.debug(f"Getting balances for slot {slot}")
        start = time.time()
        data = bacon.get_validator_balances(slot)["data"]
        log.debug(f"Got balances for slot {slot} in {time.time() - start}s")
        return data

    def get_general_data(self, slot):
        log.debug(f"Getting general data for slot {slot}")
        start = time.time()
        data = bacon.get_validators(slot)["data"]
        log.debug(f"Got general data for slot {slot} in {time.time() - start}s")
        return data

    @tasks.loop(seconds=60 ** 2)
    async def run_loop(self):
        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()
        futures = [loop.run_in_executor(executor, self.cache_embed)]
        try:
            await asyncio.gather(*futures)
        except Exception as err:
            await report_error(err)

    def cache_embed(self):
        # get current slot
        current = int(bacon.get_block("head")["data"]["message"]["slot"])
        current_epoch = current // 32
        epochs_per_day = (60 / 12) / 32 * 60 * 24
        # get balances now
        current_balances = self.get_balances(current)
        # get balances a week ago
        last_week = current - int(60 / 12 * 60 * 24 * 7)
        last_week_data = self.get_general_data(last_week)
        # get all validators from db
        validators = self.sync_db.minipools.distinct("validator")
        # filter
        last_week_data = [v for v in last_week_data if int(v["index"]) in validators]
        last_week_validators = {}
        for v in last_week_data:
            index = int(v["index"])
            # split for performance reasons
            balance = to_float(v["balance"], 9)
            days_active = (current_epoch - int(v["validator"]["activation_epoch"])) / epochs_per_day
            if balance <= 16 or days_active < 7:
                continue
            last_week_validators[index] = {
                "balance"    : balance,
                "days_active": days_active
            }
        last_week_indexes = list(last_week_validators.keys())
        # now get their balances from the current slot
        current_validators = {
            int(v["index"]): to_float(v["balance"], 9) for v in current_balances if int(v["index"]) in last_week_indexes
        }
        # generate new dictonary with validator index as key and current and last week balances as values
        balances = {
            i: {
                "current"       : current_validators[i],
                "last_week"     : last_week_validators[i]["balance"],
                "daily_earnings": (current_validators[i] - 32) / last_week_validators[i]["days_active"]
            } for i in last_week_indexes}

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
        for i, (validator, data) in enumerate(sorted_validators[:10]):
            desc += f"\n{f'#{i + 1}':>5}: {validator} - {data['current']:.2f}ETH (APR: {data['apr']:>5.2f}%)"
        desc += f"\n{'...':>5}"
        for i, (validator, data) in enumerate(sorted_validators[-10:]):
            i = len(sorted_validators) - 10 + i
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
        for i, (validator, data) in enumerate(sorted_validators[:10]):
            desc += f"\n{f'#{i + 1}':>5}: {validator} - {data['current']:.2f}ETH ({data['daily_earnings']:.5f} ETH/day)"
        desc += f"\n{'...':>5}"
        for i, (validator, data) in enumerate(sorted_validators[-10:]):
            i = len(sorted_validators) - 10 + i
            desc += f"\n{f'#{i}':>5}: {validator} - {data['current']:.2f}ETH ({data['daily_earnings']:.5f} ETH/day)"
        desc += "\n```"
        e.description = desc

        self.sync_db.leaderboard.update_one(
            {"_id": "leaderboard_daily_earnings"},
            {"$set": {"embed": e.to_dict()}},
            upsert=True
        )

        log.debug(f"Cached embeds for slot {current}")

    @slash_command(guild_ids=guilds)
    async def leaderboard(self,
                          ctx,
                          all_time: Option(
                              bool,
                              default=False,
                              required=False)):
        await ctx.defer(ephemeral=is_hidden(ctx))

        # get embed from db
        target = "leaderboard_7days" if not all_time else "leaderboard_daily_earnings"
        embed_dict = await self.db.leaderboard.find_one({"_id": target})
        if embed_dict is None:
            await ctx.respond("Leaderboard is not cached yet. Please wait a minute and try again.")
            return

        # generate embed from dict
        e = Embed.from_dict(embed_dict["embed"])
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(Leaderboard(bot))
