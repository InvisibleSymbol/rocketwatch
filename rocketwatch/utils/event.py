from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from discord.ext import commands
from eth_typing import BlockNumber

from utils.shared_w3 import w3
from utils.cfg import cfg
from utils.embeds import Embed
from utils.image import Image
from rocketwatch import RocketWatch


@dataclass(slots=True)
class Event:
    embed: Embed
    topic: str
    event_name: str
    unique_id: str
    block_number: int
    transaction_index: int = 999
    event_index: int = 999
    attachment: Optional[Image] = None

    def get_score(self):
        return (10**9 * self.block_number) + (10**5 * self.transaction_index) + self.event_index

class EventPlugin(commands.Cog):
    def __init__(self, bot: RocketWatch, rate_limit=timedelta()):
        self.bot = bot
        self.rate_limit = rate_limit
        self.lookback_distance: int = cfg["events.look_back_distance"]
        self.last_served_block = w3.eth.get_block(cfg["events.genesis"]).number
        self._pending_block = self.last_served_block
        self._last_run = datetime.now() - rate_limit

    def get_new_events(self) -> list[Event]:
        now = datetime.now()
        if (now - self._last_run) < self.rate_limit:
            return []

        self._last_run = now
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
