import os
import time
from datetime import datetime

import humanize
import psutil
import pytz
import requests
import uptime
from discord import Embed
from discord.ext import commands
from discord_slash import cog_ext

from utils import readable
from utils.cfg import cfg
from utils.readable import etherscan_url
from utils.slash_permissions import guilds

psutil.getloadavg()
BOOT_TIME = time.time()


class About(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.process = psutil.Process(os.getpid())

    @cog_ext.cog_slash(guild_ids=guilds)
    async def dev_time(self, ctx):
        """Bot and Server Information"""
        dev_time = datetime.now(tz=pytz.timezone("Australia/Lindeman"))
        await ctx.send(dev_time.strftime("It's currently %A at %H:%M for the devs"))


def setup(bot):
    bot.add_cog(About(bot))
