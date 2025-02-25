import asyncio
import logging
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from typing import Optional, cast, Any

import pymongo
import cronitor
from discord.ext import commands, tasks
from eth_typing import BlockIdentifier, BlockNumber
from motor.motor_asyncio import AsyncIOMotorClient
from web3.datastructures import MutableAttributeDict as aDict

from plugins.deposit_pool import deposit_pool
from plugins.support_utils.support_utils import generate_template_embed
from utils.cfg import cfg
from utils.embeds import assemble, Embed
from utils.event import EventPlugin
from utils.shared_w3 import w3

log = logging.getLogger("core")
log.setLevel(cfg["log_level"])

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
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).rocketwatch
        self.head_block: BlockIdentifier = cfg["events.genesis"]
        self.block_batch_size = cfg["events.block_batch_size"]
        self.monitor = cronitor.Monitor('gather-new-events', api_key=cfg["cronitor_secret"])
        self.run_loop.start()

    @tasks.loop(seconds=10.0)
    async def run_loop(self) -> None:
        p_id = time.time()
        self.monitor.ping(state='run', series=p_id)
        self.state = self.State.OK

        try:
            await self.gather_new_events()
            await self.process_event_queue()
            await self.update_status_message()
            self.monitor.ping(state="complete", series=p_id)
            return
        except Exception as err:
            self.state = self.State.ERROR
            await self.bot.report_error(err)

        try:
            await self.show_service_interrupt()
        except Exception as err:
            await self.bot.report_error(err)

        self.monitor.ping(state="fail", series=p_id)
        await asyncio.sleep(30)

    async def gather_new_events(self) -> None:
        log.info("Gathering messages from submodules")
        log.debug(f"{self.head_block = }")

        latest_block = w3.eth.get_block_number()
        submodules = [cog for cog in self.bot.cogs.values() if isinstance(cog, EventPlugin)]
        log.debug(f"Running {len(submodules)} submodules")

        if self.head_block == "latest":
            # already caught up to head, just fetch new events
            target_block = "latest"
            to_block = latest_block
            gather_fns = [sm.get_new_events for sm in submodules]
            # prevent losing state if process is interrupted before updating db
            self.head_block = cfg["events.genesis"]
        else:
            # behind chain head, let's see how far
            last_event_entry = await self.db.event_queue.find().sort(
                "block_number", pymongo.DESCENDING
            ).limit(1).to_list(None)
            if last_event_entry:
                self.head_block = max(self.head_block, last_event_entry[0]["block_number"])

            last_checked_entry = await self.db.last_checked_block.find_one({"_id": "events"})
            if last_checked_entry:
                self.head_block = max(self.head_block, last_checked_entry["block"])

            if (latest_block - self.head_block) < self.block_batch_size:
                # close enough to catch up in a single request
                target_block = "latest"
                to_block = latest_block
            else:
                # too far, advance one batch
                target_block = self.head_block + self.block_batch_size
                to_block = target_block

            from_block = cast(BlockNumber, self.head_block + 1)
            log.info(f"Checking block range [{from_block}, {to_block}]")

            gather_fns = []
            for sm in submodules:
                fn = partial(sm.get_past_events, from_block=from_block, to_block=to_block)
                gather_fns.append(fn)

        log.debug(f"{target_block = }")

        try:
            with ThreadPoolExecutor() as executor:
                loop = asyncio.get_running_loop()
                futures = [loop.run_in_executor(executor, gather_fn) for gather_fn in gather_fns]
                results = await asyncio.gather(*futures)
        except Exception as err:
            log.exception("Failed to gather events from submodules")
            self.bot.report_error(err)
            raise err

        channels = cfg["discord.channels"]
        events: list[dict[str, Any]] = []

        for result in results:
            for event in result:
                if await self.db.event_queue.find_one({"_id": event.unique_id}):
                    log.debug(f"Event {event} already exists, skipping")
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
                    "score": event.get_score(),
                    "time_seen": datetime.now(),
                    "attachment": pickle.dumps(event.attachment) if event.attachment else None,
                    "channel_id": channel_id,
                    "message_id": None
                })

        log.info(f"{len(events)} new events gathered, updating DB")
        if events:
            await self.db.event_queue.bulk_write(list(map(pymongo.InsertOne, events)))

        self.head_block = target_block
        self.db.last_checked_block.replace_one(
            {"_id": "events"},
            {"_id": "events", "block": to_block},
            upsert=True
        )

    async def process_event_queue(self) -> None:
        log.debug("Processing events in queue")
        # get all channels with unprocessed events
        channels = await self.db.event_queue.distinct("channel_id", {"message_id": None})
        if not channels:
            log.debug("No pending events in queue")
            return

        def try_load(_entry: dict, _key: str) -> Optional[Any]:
            try:
                serialized = _entry.get(_key)
                return pickle.loads(serialized) if serialized else None
            except Exception as err:
                self.bot.report_error(err)
                return None

        for channel_id in channels:
            db_events: list[dict] = await self.db.event_queue.find(
                {"channel_id": channel_id, "message_id": None}
            ).sort("score", pymongo.ASCENDING).to_list(None)
            channel = await self.bot.get_or_fetch_channel(channel_id)

            log.debug(f"Found {len(db_events)} events for channel {channel_id}.")

            if channel_id == self.channels["default"] in channels:
                if state_message := await self.db.state_messages.find_one({"_id": "state"}):
                    msg = await channel.fetch_message(state_message["message_id"])
                    await msg.delete()
                    await self.db.state_messages.delete_one({"_id": "state"})

            for event_entry in db_events:
                embed = try_load(event_entry, "embed")
                attachment = try_load(event_entry, "attachment")

                if embed and attachment:
                    file_name = event_entry["event_name"]
                    file = attachment.to_file(file_name)
                    embed.set_image(url=f"attachment://{file_name}.png")
                else:
                    file = None

                # post event message
                send_silent: bool = ("debug" in event_entry["event_name"])
                msg = await channel.send(embed=embed, file=file, silent=send_silent)
                # add message id to event
                await self.db.event_queue.update_one(
                    {"_id": event_entry["_id"]},
                    {"$set": {"message_id": msg.id}}
                )

        log.info("Processed all events in queue")

    async def update_status_message(self) -> None:
        state_message = await self.db.state_messages.find_one({"_id": "state"})

        if not cfg["events.status_message"]:
            await self._replace_or_add_status(None, state_message)
            return

        if state_message:
            # only update once every 60 seconds
            age = datetime.now() - state_message["sent_at"]
            if (age < timedelta(seconds=60)) and (state_message["state"] == str(self.State.OK)):
                return

        if not (embed := await generate_template_embed(self.db, "announcement")):
            embed = await deposit_pool.get_dp()
            embed.title = ":rocket: Live Deposit Pool Status"

        embed.timestamp = datetime.now()
        embed.set_footer(text=(
            f"Tracking {cfg['rocketpool.chain']} "
            f"using {len(self.bot.cogs)} plugins"
        ))
        for field in cfg["events.status_message.fields"]:
            embed.add_field(name=field["name"], value=field["value"])

        await self._replace_or_add_status(embed, state_message)

    async def show_service_interrupt(self) -> None:
        state_message = await self.db.state_messages.find_one({"_id": "state"})
        if state_message and (state_message["state"] == str(self.state.ERROR)):
            return

        embed = assemble(aDict({"event_name": "service_interrupted"}))
        await self._replace_or_add_status(embed, state_message)

    async def _replace_or_add_status(self, embed: Optional[Embed], prev_status: Optional[dict]) -> None:
        new_channel_id = self.channels["default"]
        prev_channel_id = (prev_status or {}).get("channel_id", new_channel_id)

        if embed and prev_status and (prev_channel_id == new_channel_id):
            channel = await self.bot.get_or_fetch_channel(prev_channel_id)
            msg = await channel.fetch_message(prev_status["message_id"])
            await msg.edit(embed=embed)
            await self.db.state_messages.update_one(
                {"_id": "state"},
                {"$set": {"sent_at": datetime.now(), "state": str(self.state)}}
            )
            return

        if prev_status:
            channel = await self.bot.get_or_fetch_channel(prev_channel_id)
            msg = await channel.fetch_message(prev_status["message_id"])
            await msg.delete()
            await self.db.state_messages.delete_one({"_id": "state"})

        if embed:
            channel = await self.bot.get_or_fetch_channel(new_channel_id)
            msg = await channel.send(embed=embed)
            await self.db.state_messages.insert_one({
                "_id": "state",
                "channel_id": new_channel_id,
                "message_id": msg.id,
                "sent_at": datetime.now(),
                "state": str(self.state)
            })

    def cog_unload(self) -> None:
        self.state = self.State.STOPPED
        self.run_loop.cancel()


async def setup(bot):
    await bot.add_cog(Core(bot))
