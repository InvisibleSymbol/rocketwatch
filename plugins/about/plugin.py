import os
import time

import humanize
import psutil
import uptime
from discord import Embed
from discord.ext import commands
from discord_slash import cog_ext

from utils import readable
from utils.slash_permissions import guilds

psutil.getloadavg()
BOOT_TIME = time.time()


class Stats(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.process = psutil.Process(os.getpid())

  @cog_ext.cog_slash(guild_ids=guilds)
  async def about(self, ctx):
    """Information about this bot"""
    await ctx.send("TBA", hidden=True)

  @cog_ext.cog_slash(guild_ids=guilds)
  async def stats(self, ctx):
    """System and Server Statistics"""
    embed = Embed()

    embed.add_field(name="CPU", value=f"{psutil.cpu_percent():.2f}%")
    embed.add_field(name="System Memory", value=f"{psutil.virtual_memory().percent}%")
    embed.add_field(name="Process Memory", value=f"{humanize.naturalsize(self.process.memory_info().rss)}")

    load = psutil.getloadavg()
    embed.add_field(name="1min load", value=f"{load[0]}")
    embed.add_field(name="5min load", value=f"{load[1]}")
    embed.add_field(name="15min load", value=f"{load[2]}")
    bot_uptime = time.time() - BOOT_TIME
    embed.add_field(name="Bot Uptime", value=f"{readable.uptime(bot_uptime)}")
    system_uptime = uptime.uptime()
    embed.add_field(name="System Uptime", value=f"{readable.uptime(system_uptime)}")

    await ctx.send(embed=embed, hidden=True)


def setup(bot):
  bot.add_cog(Stats(bot))
