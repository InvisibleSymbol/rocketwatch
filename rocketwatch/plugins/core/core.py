import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import cronitor
import motor.motor_asyncio
from discord.ext import commands, tasks
from web3.datastructures import MutableAttributeDict as aDict

from plugins.deposit_pool import deposit_pool
from plugins.queue import queue
from plugins.support_utils.support_utils import generate_template_embed
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble
from utils.get_or_fetch import get_or_fetch_channel
from utils.reporter import report_error
from utils.rocketpool import rp
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
        self.previous_run = time.time()
        # gather all currently cogs with the Queued prefix
        self.submodules = None
        self.speed_limit = 5

        if not self.run_loop.is_running():
            self.run_loop.start()

    @tasks.loop(seconds=10.0)
    async def run_loop(self):
        if not self.submodules:
            self.submodules = [cog for cog in self.bot.cogs if cog.startswith("Queued")]
        p_id = time.time()
        if p_id - self.previous_run < self.speed_limit:
            log.debug("skipping core update loop")
            return
        self.previous_run = p_id
        monitor.ping(state='run', series=p_id)
        # try to gather new events
        try:
            await self.gather_new_events()
        except Exception as err:
            self.state = "ERROR"
            await report_error(err)
        # process the messages as long as we are in a non-error state
        if self.state != "ERROR":
            try:
                await self.process_event_queue()
            except Exception as err:
                await report_error(err)
        # update the state message
        try:
            await self.update_state_message()
        except Exception as err:
            await report_error(err)
        self.speed_limit = 5 if self.state == "OK" else 30
        monitor.ping(state='fail' if self.state == "ERROR" else 'complete', series=p_id)

    async def update_state_message(self):
        # get the state message from the db
        state_message = await self.db.state_messages.find_one({"_id": "state"})
        channel = await get_or_fetch_channel(self.bot, self.channels["default"])
        # return if we are currently displaying an error message, and it's still active
        if self.state == "ERROR":
            # send state message if state changed to ERROR
            embed = assemble(aDict({
                "event_name": "service_interrupted"
            }))
            if state_message and state_message["state"] != "ERROR":
                msg = await channel.fetch_message(state_message["message_id"])
                await msg.edit(embed=embed)
                await self.db.state_messages.update_one({"_id": "state"},
                                                        {"$set": {"sent_at": time.time(), "state": self.state}})
            elif not state_message:
                msg = await channel.send(embed=embed)
                await self.db.state_messages.insert_one({
                    "_id"       : "state",
                    "message_id": msg.id,
                    "state"     : self.state,
                    "sent_at"   : time.time()
                })
            return
        elif self.state == "OK":
            if not cfg["core.status_message.fields"]:
                if state_message:
                    msg = await channel.fetch_message(state_message["message_id"])
                    await msg.delete()
                    await self.db.state_messages.delete_one({"_id": "state"})
                return
            # if the state message is less than 1 minute old, do nothing
            if state_message and time.time() - state_message["sent_at"] < 60 and state_message["state"] == "OK":
                return
            if tmp := await generate_template_embed(self.db, "announcement"):
                e = tmp
            else:
                e, _ = await deposit_pool.get_dp()
                e.title = ":rocket: Live Deposit Pool Status"
            e.timestamp = datetime.now()
            e.set_footer(
                text=f"Currently tracking {cfg['rocketpool.chain'].capitalize()} "
                     f"using {len(self.submodules)} submodules "
                     f"and {len(self.bot.cogs)} plugins"
            )
            for field in cfg["core.status_message.fields"]:
                e.add_field(name=field["name"], value=field["value"])
            if state_message:
                msg = await channel.fetch_message(state_message["message_id"])
                await msg.edit(embed=e)
                await self.db.state_messages.update_one({"_id": "state"},
                                                        {"$set": {"sent_at": time.time(), "state": self.state}})
            else:
                msg = await channel.send(embed=e)
                await self.db.state_messages.insert_one({
                    "_id"       : "state",
                    "message_id": msg.id,
                    "state"     : self.state,
                    "sent_at"   : time.time()
                })

    async def gather_new_events(self):
        log.info("Gathering messages from submodules")
        self.state = "OK"

        log.debug(f"Running {len(self.submodules)} submodules...")

        executor = ThreadPoolExecutor()
        loop = asyncio.get_event_loop()

        try:
            futures = [loop.run_in_executor(executor, self.bot.cogs[submodule].run_loop) for submodule in self.submodules]
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
            events = await self.db.event_queue.find({"channel_id": channel, "processed": False}).sort(
                [("score", 1)]).to_list(None)
            log.debug(f"{len(events)} Events found for channel {channel}.")
            target_channel = await get_or_fetch_channel(self.bot, channel)

            if channel == self.channels["default"]:
                # get the current state message
                state_message = await self.db.state_messages.find_one({"_id": "state"})
                if state_message:
                    msg = await target_channel.fetch_message(state_message["message_id"])
                    await msg.delete()
                    await self.db.state_messages.delete_one({"_id": "state"})

            for i, event in enumerate(events):
                e = Response.get_embed(event)
                msg = await target_channel.send(embed=e,silent="debug" in event["event_name"])
                # mark event as processed
                await self.db.event_queue.update_one({"_id": event["_id"]}, {"$set": {"processed": True, "message_id": msg.id}})
        log.info("Processed all events in event_queue collection.")

    def cog_unload(self):
        self.state = "STOPPED"
        self.run_loop.cancel()


async def setup(bot):
    await bot.add_cog(Core(bot))
