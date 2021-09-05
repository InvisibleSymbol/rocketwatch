import os

import humanize
import psutil
from discord import Embed
from discord.ext import commands

psutil.getloadavg()


class Stats(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.process = psutil.Process(os.getpid())

  @commands.command(brief="Show Bot stats")
  @commands.cooldown(1, 2, commands.BucketType.guild)
  async def stats(self, ctx):
    embed = Embed()

    embed.add_field(name="CPU", value=f"{psutil.cpu_percent():.2f}%", inline=True)
    embed.add_field(name="System Memory", value=f"{psutil.virtual_memory().percent}%", inline=True)
    embed.add_field(name="Process Memory", value=f"{humanize.naturalsize(self.process.memory_info().rss)}", inline=True)

    load = psutil.getloadavg()
    embed.add_field(name="1min load", value=f"{load[0]}", inline=True)
    embed.add_field(name="5min load", value=f"{load[1]}", inline=True)
    embed.add_field(name="15min load", value=f"{load[2]}", inline=True)

    await ctx.channel.send(embed=embed)


def setup(bot):
  bot.add_cog(Stats(bot))
