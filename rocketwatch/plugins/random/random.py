import os
import time
from datetime import datetime

import psutil
import pytz
from discord.ext import commands
from discord_slash import cog_ext

from utils.slash_permissions import guilds

psutil.getloadavg()
BOOT_TIME = time.time()


class About(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.process = psutil.Process(os.getpid())

    @cog_ext.cog_slash(guild_ids=guilds)
    async def dev_time(self, ctx):
        """Ever wondered what time it is in Upside-down land? Well worry no more, this command is here to help!"""
        dev_time = datetime.now(tz=pytz.timezone("Australia/Lindeman"))
        await ctx.send(dev_time.strftime("It's currently %A at %H:%M for the devs"))


def setup(bot):
    bot.add_cog(About(bot))
