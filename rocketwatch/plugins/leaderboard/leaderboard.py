import logging
import time

import inflect
from discord.commands import slash_command
from discord.ext import commands, tasks
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import Embed
from utils.make_async import make_async
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

        if not self.cache_embed.is_running() and bot.is_ready():
            self.cache_embed.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.cache_embed.is_running():
            return
        self.cache_embed.start()

    @make_async
    def get_balances(self, slot):
        log.debug(f"Getting balances for slot {slot}")
        start = time.time()
        data = bacon.get_validator_balances(slot)["data"]
        log.debug(f"Got balances for slot {slot} in {time.time() - start}s")
        return data

    @tasks.loop(seconds=60 ** 2)
    async def cache_embed(self):
        # get current slot
        current = int(bacon._make_get_request("/eth/v2/beacon/blocks/head")["data"]["message"]["slot"])
        # get balances now
        current_balances = await self.get_balances(current)
        # get balances a week ago
        last_week = current - int(60 / 12 * 60 * 24 * 7)
        last_week_balances = await self.get_balances(last_week)
        # get all validators from db
        validators = await self.db.minipools.distinct("validator")
        # get all validators that are in the balances' dict from the last week
        last_week_validators = {
            int(v["index"]): to_float(v["balance"], 9) for v in last_week_balances if all(
                [
                    int(v["index"]) in validators,
                    to_float(v["balance"], 9) > 16
                ])
        }
        last_week_indexes = list(last_week_validators.keys())
        # now get their balances from the current slot
        current_validators = {
            int(v["index"]): to_float(v["balance"], 9) for v in current_balances if int(v["index"]) in last_week_indexes
        }
        # generate new dictonary with validator index as key and current and last week balances as values
        balances = {
            i: {
                "current"  : current_validators[i],
                "last_week": last_week_validators[i]
            } for i in last_week_indexes}

        # calculate APR attribute
        for i in balances:
            # get percentage change between first and last datapoint
            total_change_percent = (balances[i]["current"] - balances[i]["last_week"]) / balances[i]["last_week"]

            # extrapolate change to 1 year
            yearly_change = total_change_percent * 52
            balances[i]["apr"] = yearly_change * 100

        # get sorted list of validators
        sorted_balances = sorted(balances.items(), key=lambda x: x[1]["apr"], reverse=True)

        # generate embed
        e = Embed(
            title="APR Leaderboard (last 7 days)",
        )
        e.set_footer(text=f"Last updated Slot {current}")

        # add top 10 validators
        desc = "```\n"
        for i, (validator, data) in enumerate(sorted_balances[:10]):
            desc += f"\n{f'#{i + 1}':>5}: {validator} - {data['current']:.2f}ETH (APR: {data['apr']:>5.2f}%)"
        desc += f"\n{'...':>5}"
        for i, (validator, data) in enumerate(sorted_balances[-10:]):
            i = len(sorted_balances) - i
            desc += f"\n{f'#{i}':>5}: {validator} - {data['current']:.2f}ETH (APR: {data['apr']:>5.2f}%)"
        desc += "\n```"
        e.description = desc

        await self.db.leaderboard.update_one(
            {"_id": "leaderboard"},
            {"$set": {"embed": e.to_dict()}},
            upsert=True
        )

    @slash_command(guild_ids=guilds)
    async def leaderboard(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))

        # get embed from db
        embed_dict = await self.db.leaderboard.find_one({"_id": "leaderboard"})
        if embed_dict is None:
            await ctx.respond("Leaderboard is not cached yet. Please wait a minute and try again.")
            return

        # generate embed from dict
        e = Embed.from_dict(embed_dict["embed"])
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(Leaderboard(bot))
