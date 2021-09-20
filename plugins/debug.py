import os
import random

import psutil
from discord.ext import commands

from utils.slash_commands import owner_only_slash

psutil.getloadavg()


class Debug(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.process = psutil.Process(os.getpid())

  @owner_only_slash()
  async def raise_exception(self, ctx):
    with open(str(random.random()), "rb"):
      raise Exception("this should never happen wtf is your filesystem")


def setup(bot):
  bot.add_cog(Debug(bot))
