from abc import abstractmethod

from discord.ext import commands

from rocketwatch import RocketWatch
from utils.embeds import Embed


class StatusPlugin(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @staticmethod
    @abstractmethod
    async def get_status_message() -> Embed:
        pass
