from abc import abstractmethod
from datetime import datetime, timedelta
from typing import Optional

from discord.ext import commands
from eth_typing import BlockNumber

from utils.shared_w3 import w3
from utils.cfg import cfg
from utils.embeds import Embed
from utils.image import Image
from rocketwatch import RocketWatch


class Event:
    def __init__(
            self,
            embed: Embed,
            topic: str,
            event_name: str,
            unique_id: str,
            block_number: int,
            transaction_index: int = 999,
            event_index: int = 999,
            attachment: Optional[Image] = None
    ):
        self.embed = embed.copy()
        self.topic = topic
        self.event_name = event_name
        self.unique_id = unique_id
        self.block_number = block_number
        self.transaction_index = transaction_index
        self.attachment = attachment
        self.time_seen = datetime.now()
        self.score = (10**9 * block_number) + (10**5 * transaction_index) + event_index

        if self.embed.footer and self.embed.footer.text:
            self.embed.set_footer_parts([f"score: {self.score}"])


class EventSubmodule(commands.Cog):
    def __init__(self, bot: RocketWatch, rate_limit=timedelta()):
        self.bot = bot
        self.rate_limit = rate_limit
        self.lookback_distance: int = cfg["events.look_back_distance"]
        self.last_served_block = w3.eth.get_block(cfg["events.genesis"]).number
        self._pending_block = self.last_served_block
        self._last_ran = datetime.now() - rate_limit

    def get_new_events(self) -> list[Event]:
        if (datetime.now() - self._last_ran) < self.rate_limit:
            return []

        self._last_ran = datetime.now()
        self._pending_block = w3.eth.get_block_number()
        events = self._get_new_events()
        self.last_served_block = self._pending_block
        return events

    @abstractmethod
    def _get_new_events(self) -> list[Event]:
        pass

    def get_past_events(self, from_block: BlockNumber, to_block: BlockNumber) -> list[Event]:
        self._pending_block = max(self.last_served_block, to_block)
        events = self._get_past_events(from_block, to_block)
        self.last_served_block = self._pending_block
        return events

    def _get_past_events(self, from_block: BlockNumber, to_block: BlockNumber) -> list[Event]:
        return []
