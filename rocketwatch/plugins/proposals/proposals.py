import asyncio
import logging
from io import BytesIO

import aiohttp
from discord import Embed, Color, File
from discord.commands import slash_command
from discord.ext import commands
from matplotlib import pyplot as plt
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.slash_permissions import guilds, owner_only_slash
from utils.visibility import is_hidden

log = logging.getLogger("proposals")
log.setLevel(cfg["log_level"])


class Proposals(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)
        self.slots_url = "https://beaconcha.in/blocks/data"
        self.validator_url = "https://beaconcha.in/api/v1/validator/"
        # connect to local mongodb
        self.db = AsyncIOMotorClient("mongodb://mongodb:27017").get_database("rocketwatch")
        self.collection = self.db.proposals
        self.tmp_data = {}

    @owner_only_slash()
    async def drop_proposals(self, ctx):
        await ctx.defer()
        log.info("dropping all proposals")
        await self.collection.delete_many({})
        log.info("finished dropping all proposals")
        await ctx.respond("dropped all proposals")

    async def gather_new_proposals(self):
        log.info("getting proposals with rocket pool graffiti...")
        amount = 100
        index = 0
        start = 0
        should_continue = True
        while True:
            log.debug(f"requesting proposals {start} to {start + amount}")
            async with aiohttp.ClientSession() as session:
                res = await session.get(
                    self.slots_url,
                    params={
                        "draw"         : index,
                        "start"        : start,
                        "length"       : amount,
                        "search[value]": "RP-"
                    })
                res = await res.json()
            proposals = res["data"]
            for entry in proposals:
                slot = int(entry[1].split(">")[-2].split("<")[0].replace(",", ""))

                # break if the slot is already in the database
                if await self.collection.count_documents({"slot": slot}) > 0:
                    log.debug(f"slot {slot} already in the database")
                    should_continue = False
                    continue

                graffiti = entry[11].split(">")[-3].split("<")[0]
                if graffiti.startswith("RP-"):
                    should_continue = True
                    parts = graffiti.split(" ")
                    data = {
                        "slot"     : slot,
                        "validator": int(entry[4].split("/validator/")[1].split("\">")[0]),
                        "client"   : parts[0].split("-")[1], "version": parts[1]
                    }
                    if len(parts) > 2:
                        data["comment"] = " ".join(parts[2:]).lstrip("(").rstrip(")")
                    await self.collection.replace_one({"slot": data["slot"]}, data, upsert=True)

            if len(proposals) != 100 or not should_continue:
                log.debug(f"stopping proposal gathering: {len(proposals)=}, {should_continue=}")
                break
            index += 1
            start += amount
            await asyncio.sleep(5)
        log.info("finished gathering new proposals")

    async def gather_pubkeys(self):
        log.info("getting pubkeys for new validators...")
        validators = await self.collection.distinct("validator", {"pubkey": {"$exists": False}})
        # iterate in batches of 100
        for i in range(0, len(validators), 100):
            log.debug(f"requesting pubkeys {i} to {i + 100}")
            validator_ids = [str(x) for x in validators[i:i + 100]]
            async with aiohttp.ClientSession() as session:
                res = await session.get(self.validator_url + ",".join(validator_ids))
                res = await res.json()
            data = res["data"]
            # handle when we only get a single validator back
            if not isinstance(data, list):
                data = [data]
            for validator_id, validator_data in zip(validator_ids, data):
                await self.collection.update_many({"validator": int(validator_id)},
                                                  {"$set": {"pubkey": validator_data["pubkey"]}})
            await asyncio.sleep(5)
        log.info("finished gathering pubkeys")

    @slash_command(guild_ids=guilds)
    async def version_chart(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        await self.gather_new_proposals()
        await self.gather_pubkeys()

        e = Embed(title="Version Chart", color=self.color)

        # get proposals
        proposals = await self.collection.find().sort("slot", 1).to_list(None)
        batch_size = int(60 / 12 * 60 * 24 * 2)
        data = {}
        versions = []
        for proposal in proposals:
            slot = proposal["slot"] // batch_size * batch_size
            if slot not in data:
                data[slot] = {}
            if proposal["version"] not in data[slot]:
                data[slot][proposal["version"]] = 0
            if proposal["version"] not in versions:
                versions.append(proposal["version"])
            data[slot][proposal["version"]] += 1

        latest_slot = int(max(data.keys()))
        versions_from_latest = [x for x in versions if x in data[latest_slot]]
        # show stats from the latest batch
        descriptions = [
            f"{version}: {data[latest_slot][version]}" for version in versions_from_latest
        ]

        descriptions = "```\n" + "\n".join(descriptions) + "```"
        e.add_field(name=f"Statistics for slots {latest_slot} - {latest_slot + batch_size}", value=descriptions)

        # normalize data
        for slot, value in data.items():
            total = sum(data[slot].values())
            for version in data[slot]:
                value[version] /= total

        # use plt.stackplot to stack the data
        x = list(data.keys())
        y = {v: [] for v in versions}
        for slot, value_ in data.items():
            for version in versions:
                y[version].append(value_.get(version, 0))

        plt.stackplot(x, *y.values(), labels=versions)
        plt.title("Version Chart")
        plt.xlabel("slot")
        plt.ylabel("Percentage")
        plt.legend(loc="upper left")
        plt.tight_layout()

        # respond with image
        img = BytesIO()
        plt.savefig(img, format="png")
        img.seek(0)
        plt.close()
        e.set_image(url="attachment://chart.png")

        # send data
        await ctx.respond(embed=e, file=File(img, "chart.png"))
        img.close()


def setup(bot):
    bot.add_cog(Proposals(bot))
