import logging

from discord.ext import commands
from discord.ext.commands import hybrid_command, Context
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed, el_explorer_url
from utils.readable import render_tree_legacy
from utils.shared_w3 import w3
from utils.visibility import is_hidden

log = logging.getLogger("beacon_states")
log.setLevel(cfg["log_level"])


class BeaconStates(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb.uri"]).get_database("rocketwatch")

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
        description += render_tree_legacy(data, "Minipool States")
        if 0 < len(exiting_valis) <= 24:
            description += "\n\n--- Exiting Minipools ---\n\n"
            # array of validator attribute, sorted by index
            valis = sorted([v["validator_index"] for v in exiting_valis], key=lambda x: x)
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
        else:
            description += "```"

        embed.description = description
        await ctx.send(embed=embed)


async def setup(self):
    await self.add_cog(BeaconStates(self))
