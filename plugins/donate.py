import os
import time

import psutil
from discord import Embed
from discord.ext import commands

from utils.slash_commands import default_slash

psutil.getloadavg()
BOOT_TIME = time.time()


class Stats(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.process = psutil.Process(os.getpid())

  @default_slash()
  async def donate(self, ctx):
    """Donate to the Bot Developer"""
    embed = Embed()
    embed.description = "Donation Address: **`0x87FF5B8ccFAeEC77b2B4090FD27b11dA2ED808Fb`** ([Ownership Proof](https://etherscan.io/verifySig/3414))"
    embed.set_footer(text="Ethereum or Ethereum-based Rollups preferred, but others are ofc fine as well")
    content = "**Thank you for support! <3**\n" \
              "I hope my bot has been useful for you, it has been a fun experience building this!\n" \
              "If you can, any donation helps me keep doing what I love! (Also helps me pay for server bills lol)"
    await ctx.send(
      content,
      embed=embed,
      hidden=True)


def setup(bot):
  bot.add_cog(Stats(bot))
