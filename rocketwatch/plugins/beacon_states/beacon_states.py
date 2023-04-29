import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import pymongo
from discord.ext import commands, tasks
from discord.ext.commands import hybrid_command, Context
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, el_explorer_url
from utils.readable import render_tree
from utils.reporter import report_error
from utils.shared_w3 import bacon, w3
from utils.time_debug import timerun
from utils.visibility import is_hidden

log = logging.getLogger("beacon_states")
log.setLevel(cfg["log_level"])


class BeaconStates(commands.Cog):
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
    def get_validators(self):
        # get all validator indexes from db
        vali_indexes = self.sync_db.minipools.find({}).distinct("validator")
        res = bacon.get_validators("head", ids=vali_indexes)["data"]
        # we get back an array, turn into dict of index
        res = {int(v["index"]): v for v in res}
        return res

    @tasks.loop(seconds=5 * 60)
    async def run_loop(self):
        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()
        futures = [loop.run_in_executor(executor, self.update_states)]
        try:
            await asyncio.gather(*futures)
        except Exception as err:
            await report_error(err)

    def update_states(self):
        log.info("Updating validator states")
        a = self.get_validators()
        # we get back a dict of index => {status: string}
        # we want to update the db with this using bulk write
        batch = [
            pymongo.UpdateOne(
                {
                    "validator": index
                },
                {
                    "$set": {
                        "status"    : vali["status"],
                        "is_slashed": vali["validator"]["slashed"],
                        "balance"   : solidity.to_float(vali["balance"], 9)
                    }
                }
            ) for index, vali in a.items()]
        self.sync_db.minipools.bulk_write(batch, ordered=False)
        log.info(f"Updated {len(batch)} validators")

    @hybrid_command()
    async def beacon_states(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden(ctx))
        # fetch from db
        res = await self.db.minipools_new.find({
            "beacon.status": {"$exists": True}
        }).to_list(None)
        data = {
            "pending": {},
            "active" : {},
            "exiting": {},
            "exited" : {}
        }
        exiting_valis = []
        for minipool in res:
            match minipool["beacon"]["status"]:
                case "pending_initialized":
                    data["pending"]["initialized"] = data["pending"].get("initialized", 0) + 1
                case "pending_queued":
                    data["pending"]["queued"] = data["pending"].get("queued", 0) + 1
                case "active_ongoing":
                    data["active"]["ongoing"] = data["active"].get("ongoing", 0) + 1
                case "active_exiting":
                    data["exiting"]["voluntarily"] = data["exiting"].get("voluntarily", 0) + 1
                    exiting_valis.append(minipool)
                case "active_slashed":
                    data["exiting"]["slashed"] = data["exiting"].get("slashed", 0) + 1
                    exiting_valis.append(minipool)
                case "exited_unslashed" | "exited_slashed" | "withdrawal_possible" | "withdrawal_done":
                    if minipool["beacon"]["slashed"]:
                        data["exited"]["slashed"] = data["exited"].get("slashed", 0) + 1
                    else:
                        data["exited"]["voluntarily"] = data["exited"].get("voluntarily", 0) + 1
                case _:
                    logging.warning(f"Unknown status {minipool['status']}")

        embed = Embed(title="Beacon Chain Minipool States", color=0x00ff00)
        description = "```\n"
        # render dict as a tree like structure
        description += render_tree(data, "Minipool States")
        if 0 < len(exiting_valis) <= 24:
            description += "\n\n--- Exiting Minipools ---\n\n"
            # array of validator attribute, sorted by index
            valis = sorted([v["validator"] for v in exiting_valis], key=lambda x: x)
            description += ", ".join([str(v) for v in valis])
            description += "```"
        elif len(exiting_valis) > 24:
            description += "```\n**Exiting Node Operators:**\n"
            node_operators = {}
            # dedupe, add count of validators with matching node operator
            for v in exiting_valis:
                node_operators[v["node_operator"]] = node_operators.get(v["node_operator"], 0) + 1
            # turn into list
            node_operators = list(node_operators.items())
            # sort by count
            node_operators.sort(key=lambda x: x[1], reverse=True)
            description += ""
            # use el_explorer_url
            description += ", ".join([f"{el_explorer_url(w3.toChecksumAddress(v))} ({c})" for v, c in node_operators[:16]])
            # append ",…" if more than 16
            if len(node_operators) > 16:
                description += ",…"

        embed.description = description
        await ctx.send(embed=embed)

async def setup(self):
    await self.add_cog(BeaconStates(self))
