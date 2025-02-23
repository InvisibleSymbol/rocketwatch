from abc import abstractmethod
from datetime import datetime, timedelta
from typing import Optional

from discord.ext import commands

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
        self._last_ran = datetime.now() - rate_limit

    def run(self) -> list[Event]:
        if (datetime.now() - self._last_ran) < self.rate_limit:
            return []

        self._last_ran = datetime.now()
        return self._run()

    @abstractmethod
    def _run(self) -> list[Event]:
        pass
