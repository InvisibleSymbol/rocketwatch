import asyncio
import logging
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from enum import Enum
from functools import partial
from typing import Optional, cast, Any

import cronitor
import pymongo
import motor.motor_asyncio
from discord.ext import commands, tasks
from eth_typing import BlockIdentifier
from web3.datastructures import MutableAttributeDict as aDict

from plugins.deposit_pool import deposit_pool
from plugins.support_utils.support_utils import generate_template_embed
from utils.shared_w3 import w3
from utils.cfg import cfg
from utils.image import Image
from utils.embeds import assemble, Embed
from utils.event import EventSubmodule, Event

log = logging.getLogger("core")
log.setLevel(cfg["log_level"])

cronitor.api_key = cfg["cronitor_secret"]
monitor = cronitor.Monitor('gather-new-events')


class Core(commands.Cog):
    class State(Enum):
        PENDING = 0
        OK = 1
        ERROR = 2
        STOPPED = 3

        def __str__(self) -> str:
            return self.name

    def __init__(self, bot):
        self.bot = bot
        self.state = self.State.PENDING
        self.channels = cfg["discord.channels"]
        self.mongo = motor.motor_asyncio.AsyncIOMotorClient(cfg["mongodb_uri"])
        self.db = self.mongo.rocketwatch
        self.previous_run = time.time()
        self.submodules: Optional[list[EventSubmodule]] = None
        self.speed_limit = 5
        self.head_block: BlockIdentifier = cfg["events.genesis"]
        self.rpc_block_limit = cfg["events.request_block_limit"]

        self.run_loop.start()

    @tasks.loop(seconds=10.0)
    async def run_loop(self):
        if not self.submodules:
            self.submodules = [cog for cog in self.bot.cogs.values() if isinstance(cog, EventSubmodule)]

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
            self.state = self.State.ERROR
            await self.bot.report_error(err)
        # process the messages as long as we are in a non-error state
        if self.state != self.State.ERROR:
            try:
                await self.process_event_queue()
            except Exception as err:
                await self.bot.report_error(err)
        # update the state message
        try:
            await self.update_state_message()
        except Exception as err:
            await self.bot.report_error(err)
        self.speed_limit = 5 if self.state == self.State.OK else 30
        monitor.ping(state='fail' if self.state == self.State.ERROR else 'complete', series=p_id)

    async def update_state_message(self):
        # get the state message from the db
        state_message = await self.db.state_messages.find_one({"_id": "state"})
        channel = await self.bot.get_or_fetch_channel(self.channels["default"])
        # return if we are currently displaying an error message, and it's still active
        if self.state == self.State.ERROR:
            # send state message if state changed to ERROR
            embed = assemble(aDict({
                "event_name": "service_interrupted"
            }))
            if state_message and state_message["state"] != str(self.State.ERROR):
                msg = await channel.fetch_message(state_message["message_id"])
                await msg.edit(embed=embed)
                await self.db.state_messages.update_one(
                    {"_id": "state"},
                    {"$set": {"sent_at": time.time(), "state": str(self.state)}}
                )
            elif not state_message:
                msg = await channel.send(embed=embed)
                await self.db.state_messages.insert_one({
                    "_id"       : "state",
                    "message_id": msg.id,
                    "state"     : str(self.state),
                    "sent_at"   : time.time()
                })
            return
        elif self.state == self.State.OK:
            if not cfg["events.status_message.fields"]:
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
                     f"and {len(self.bot.cogs) - len(self.submodules)} plugins"
            )
            for field in cfg["events.status_message.fields"]:
                e.add_field(name=field["name"], value=field["value"])
            if state_message:
                msg = await channel.fetch_message(state_message["message_id"])
                await msg.edit(embed=e)
                await self.db.state_messages.update_one(
                    {"_id": "state"},
                    {"$set": {"sent_at": time.time(), "state": self.state}}
                )
            else:
                msg = await channel.send(embed=e)
                await self.db.state_messages.insert_one({
                    "_id"       : "state",
                    "message_id": msg.id,
                    "state"     : self.state,
                    "sent_at"   : time.time()
                })

    async def gather_new_events(self):
        log.info("Gathering messages from submodules.")
        self.state = self.State.OK
        log.debug(f"Running {len(self.submodules)} submodules")
        log.debug(f"{self.head_block = }")

        if self.head_block == "latest":
            # already caught up to head, just fetch new events
            target_block = "latest"
            gather_fns = [submodule.get_new_events for submodule in self.submodules]
        else:
            # behind chain head, let's see how far
            last_event_entry = await self.db.event_queue.find({}) \
                .sort("block_number", pymongo.DESCENDING).limit(1).to_list(None)
            last_event_block = last_event_entry[0]["block_number"] if last_event_entry else 0
            self.head_block = max(self.head_block, last_event_block)

            latest_block = w3.eth.get_block_number()
            if (latest_block - self.head_block) < self.rpc_block_limit:
                # close enough to catch up in a single request
                target_block = "latest"
                to_block = latest_block
            else:
                # too far, advance by configured maximum
                target_block = self.head_block + self.rpc_block_limit
                to_block = target_block

            from_block = self.head_block + 1
            log.debug(f"{from_block = }, {to_block = }")
            gather_fns = []
            for submodule in self.submodules:
                gather_fns.append(partial(submodule.get_past_events, from_block=from_block, to_block=to_block))

        log.debug(f"{target_block = }")

        try:
            executor = ThreadPoolExecutor()
            loop = asyncio.get_event_loop()
            futures = [loop.run_in_executor(executor, gather_fn) for gather_fn in gather_fns]
        except Exception as err:
            log.exception("Failed to prepare submodules.")
            raise err

        try:
            results: list[list[Event] | Exception] = await asyncio.gather(*futures, return_exceptions=True)
        except Exception as err:
            log.exception("Failed to gather events from submodules.")
            raise err

        channels = cfg["discord.channels"]
        events: list[dict[str, Any]] = []

        for result in results:
            # check if the result is an exception
            if isinstance(result, Exception):
                self.state = self.State.ERROR
                self.bot.report_error(result)
                raise result

            for event in result:
                if await self.db.event_queue.find_one({"_id": event.unique_id}):
                    log.debug(f"Event {event} already exists, skipping.")
                    continue

                # select channel dynamically from config based on event_name prefix
                channel_candidates = [value for key, value in channels.items() if event.event_name.startswith(key)]
                channel_id = channel_candidates[0] if channel_candidates else channels['default']
                events.append({
                    "_id": event.unique_id,
                    "embed": pickle.dumps(event.embed),
                    "topic": event.topic,
                    "event_name": event.event_name,
                    "block_number": event.block_number,
                    "score": event.score,
                    "time_seen": datetime.now(),
                    "attachment": pickle.dumps(event.attachment),
                    "channel_id": channel_id,
                    "message_id": None
                })

        log.info(f"{len(events)} new events gathered, inserting into DB.")
        if events:
            await self.db.event_queue.bulk_write(list(map(pymongo.InsertOne, events)))
        self.head_block = target_block

    async def process_event_queue(self):
        log.debug("Processing events in queue...")
        # get all channels with unprocessed events
        channels = await self.db.event_queue.distinct("channel_id", {"message_id": None})

        if not channels:
            log.debug("No pending events in queue.")
            return

        for channel in channels:
            db_events: list[dict[str]] = await self.db.event_queue.find(
                {"channel_id": channel, "message_id": None}
            ).sort("score", pymongo.ASCENDING).to_list(None)
            log.debug(f"{len(db_events)} events found for channel {channel}.")
            target_channel = await self.bot.get_or_fetch_channel(channel)

            if channel == self.channels["default"]:
                # get the current state message
                state_message = await self.db.state_messages.find_one({"_id": "state"})
                if state_message:
                    msg = await target_channel.fetch_message(state_message["message_id"])
                    await msg.delete()
                    await self.db.state_messages.delete_one({"_id": "state"})

            def try_load(_entry: dict[str], _key: str) -> Optional[object]:
                try:
                    return pickle.loads(_entry[_key])
                except Exception:
                    return None

            for event_dict in db_events:
                event = Event(
                    embed=cast(Embed, try_load(event_dict, "embed")),
                    topic=event_dict["topic"],
                    event_name=event_dict["event_name"],
                    unique_id=event_dict["_id"],
                    block_number=event_dict["block_number"],
                    attachment=cast(Image, try_load(event_dict, "attachment")),
                )

                embed = event.embed
                attachment = event.attachment
                if embed and attachment:
                    file_name = event.event_name
                    file = attachment.to_file(file_name)
                    embed.set_image(url=f"attachment://{file_name}.png")
                else:
                    file = None

                # post event message
                send_silent: bool = ("debug" in event.event_name)
                msg = await target_channel.send(embed=embed, file=file, silent=send_silent)

                # add message id to event
                await self.db.event_queue.update_one(
                    {"_id": event.unique_id},
                    {"$set": {"message_id": msg.id}}
                )

        log.info("Processed all events in queue.")

    def cog_unload(self):
        self.state = self.State.STOPPED
        self.run_loop.cancel()


async def setup(bot):
    await bot.add_cog(Core(bot))
