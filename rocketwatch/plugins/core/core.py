import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import cronitor
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

cronitor.api_key = cfg["cronitor_secret"]
monitor = cronitor.Monitor('gather-new-events')


class Core(commands.Cog):
    event_queue = []

    def __init__(self, bot):
        self.bot = bot
        self.state = "PENDING"
        self.channels = cfg["discord.channels"]
        self.mongo = motor.motor_asyncio.AsyncIOMotorClient(cfg["mongodb_uri"])
        self.db = self.mongo.rocketwatch
        # block filter
        self.block_event = w3.eth.filter("latest")

        if not self.run_loop.is_running():
            self.run_loop.start()

    @tasks.loop(seconds=10.0)
    async def run_loop(self):
        p_id = time.time()
        monitor.ping(state='run', series=p_id)
        # try to gather new events
        try:
            await self.gather_new_events()
        except Exception as err:
            self.state = "ERROR"
            await report_error(err)
        # update the state message
        try:
            await self.update_state_message()
        except Exception as err:
            await report_error(err)
        # process the messages as long as we are in a non-error state
        if self.state != "ERROR":
            try:
                await self.process_event_queue()
            except Exception as err:
                await report_error(err)
        a = monitor.ping(state='fail' if self.state == "ERROR" else 'success', series=p_id)
        log.debug(f"pushed to cronitor {self.state}: {a}")

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

    async def gather_new_events(self):
        log.info("Gathering messages from submodules")
        self.state = "OK"

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
            results = await asyncio.gather(*futures, return_exceptions=True)
        except Exception as err:
            log.error("Failed to gather submodules.")
            raise err

        tmp_event_queue = []
        for result in results:
            # check if the result is an exception
            if isinstance(result, Exception):
                self.state = "ERROR"
                log.error(f"Submodule returned an exception: {result}")
                log.exception(result)
                await report_error(result)
                continue
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
            for event in events:
                await target_channel.send(embed=Response.get_embed(event))
                # mark event as processed
                await self.db.event_queue.update_one({"_id": event["_id"]}, {"$set": {"processed": True}})

        log.info("Processed all events in event_queue collection.")

    def cog_unload(self):
        self.state = "STOPPED"
        self.run_loop.cancel()


def setup(bot):
    bot.add_cog(Core(bot))
