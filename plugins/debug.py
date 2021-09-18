import os
import random

import psutil
from discord.ext import commands

psutil.getloadavg()


class Debug(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.process = psutil.Process(os.getpid())

  @commands.is_owner()
  @commands.command(brief="Raise Exception to test Error handling", aliases=["error", "raise"])
  async def raise_exception(self, ctx):
    with open(str(random.random()), "rb"):
      raise Exception("this should never happen wtf is your filesystem")


def setup(bot):
  bot.add_cog(Debug(bot))
