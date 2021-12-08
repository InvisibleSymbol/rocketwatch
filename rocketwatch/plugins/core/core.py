import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import motor.motor_asyncio
from discord.ext import commands, tasks
from web3.datastructures import MutableAttributeDict as aDict

from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble
from utils.reporter import report_error
from utils.shared_w3 import w3

log = logging.getLogger("core")
log.setLevel(cfg["log_level"])


class Core(commands.Cog):
    event_queue = []

    def __init__(self, bot):
        self.bot = bot
        self.state = "PENDING"
        self.channels = cfg["discord.channels"]
        self.mongo = motor.motor_asyncio.AsyncIOMotorClient('mongodb://localhost:27017')
        self.db = self.mongo.rocketwatch
        # block filter
        self.block_event = w3.eth.filter("latest")

        if not self.run_loop.is_running():
            self.run_loop.start()

    @tasks.loop(seconds=30.0)
    async def run_loop(self):

        try:
            await self.gather_and_process_messages()
            await self.process_event_queue()
            self.state = "OK"
        except Exception as err:
            self.state = "ERROR"
            await report_error(err)
        try:
            await self.update_state_message()
        except Exception as err:
            await report_error(err)

    async def update_state_message(self):
        # get the state message from the db
        state_message = await self.db.state_messages.find_one({"_id": "state"})
        if self.state == "OK" and not state_message:
            return
        channel = await self.bot.fetch_channel(self.channels["default"])
        if self.state == "OK" and state_message:
            # delete state message if state changed to OK
            msg = await channel.fetch_message(state_message["message"])
            await msg.delete()
            await self.db.state_messages.delete_one({"_id": "state"})
        elif self.state == "ERROR" and not state_message:
            # send state message if state changed to ERROR
            embed = assemble(aDict({
                "event_name": "service_interrupted"
            }))
            msg = await channel.send(embed=embed)
            await self.db.state_messages.insert_one({"_id": "state", "message": msg.id})

    async def gather_and_process_messages(self):
        log.info("Gathering messages from submodules")
        # okay so it would be really cool if the bot sent a message in the default channel
        # if it fails run any submodule
        # need to keep track of the sent warning message and delete it if it works again.
        # we already need some kind of db so it doesnt sound too hard to add

        if self.state == "PENDING":
            latest_block_number = await self.db.event_queue.find_one({"_id": "block_number"})
            if latest_block_number:
                latest_block_number = latest_block_number["block_number"]
            else:
                latest_block_number = w3.eth.get_block("latest")["number"]
            look_back_distance = cfg["core.look_back_distance"]
            starting_block = latest_block_number - look_back_distance

        # gather all currently cogs with the Queued prefix
        submodules = [cog for cog in self.bot.cogs if cog.startswith("Queued")]

        log.debug(f"Running {len(submodules)} submodules...")

        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()

        try:
            futures = [loop.run_in_executor(executor, self.bot.cogs[submodule].run_loop) for submodule in submodules]
        except Exception as err:
            log.error("Failed to prepare submodules.")
            raise err

        try:
            results = await asyncio.gather(*futures)
        except Exception as err:
            log.error("Failed to gather submodules.")
            raise err

        tmp_event_queue = []
        for result in results:
            if result:
                for entry in result:
                    if await self.db.event_queue.find_one({"_id": entry.unique_id}):
                        continue
                    await self.db.event_queue.insert_one(entry.to_dict())
                    tmp_event_queue.append(entry)

        log.debug(f"{len(tmp_event_queue)} new Events gathered.")

    async def process_event_queue(self):
        log.debug("Processing events in event_queue collection...")
        # get all non-processed unique channels from event_queue
        channels = await self.db.event_queue.distinct("channel_id", {"processed": False})

        if not channels:
            log.debug("No pending events in event_queue collection.")
            return

        for channel in channels:
            events = await self.db.event_queue.find({"channel_id": channel, "processed": False}).sort([("score", 1)]).to_list(None)
            log.debug(f"{len(events)} Events found for channel {channel}.")
            target_channel = await self.bot.fetch_channel(channel)
            # fetch webhook for channel
            webhook = None
            for w in await target_channel.webhooks():
                if w.name == f"RocketWatch[{target_channel.name}]":
                    webhook = w
            if not webhook:
                log.warning(f"No webhook found for channel {channel}. Attempting to create one...")
                webhook = await target_channel.create_webhook(name=f"RocketWatch[{target_channel.name}]",
                                                              avatar=await self.bot.user.avatar.read(),
                                                              reason=f"Auto-created by RocketWatch for Channel {channel}")
                log.info(f"Created webhook {webhook.id}.")
            log.debug(webhook)
            for i in range(0, len(events), 10):
                batch = events[i:i + 10]
                log.debug(f"Sending {len(batch)} events to webhook {webhook.id}.")
                embeds = [Response.get_embed(event) for event in batch]
                await webhook.send(embeds=embeds)
                # mark batch as processed
                await self.db.event_queue.update_many({"_id": {"$in": [event["_id"] for event in batch]}},
                                                      {"$set": {"processed": True}})

        log.info("Processed all events in event_queue collection.")

    def cog_unload(self):
        self.state = "STOPPED"
        self.run_loop.cancel()


def setup(bot):
    bot.add_cog(Core(bot))
