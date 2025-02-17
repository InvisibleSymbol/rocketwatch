from abc import abstractmethod
from datetime import datetime, timedelta
from discord.ext import commands

from utils.containers import Event


class QueuedSubmodule(commands.Cog):
    def __init__(self, bot, rate_limit=timedelta()):
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
