import logging

import humanize
from discord import Activity, ActivityType
from discord.ext import commands, tasks

from utils.cfg import cfg
from utils.reporter import report_error
from utils.rocketpool import rp

log = logging.getLogger("rich_activity")
log.setLevel(cfg["log_level"])


class RichActivity(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

    if not self.run_loop.is_running() and bot.is_ready():
      self.run_loop.start()

  @commands.Cog.listener()
  async def on_ready(self):
    self.run_loop.start()

  @tasks.loop(seconds=60.0)
  async def run_loop(self):
    try:
      log.debug("Updating Discord Activity...")
      count = humanize.intcomma(rp.call("rocketMinipoolManager.getStakingMinipoolCount"))
      await self.bot.change_presence(activity=Activity(type=ActivityType.watching, name=f"{count} Minipools!"))
    except Exception as err:
      await report_error(err)

  def cog_unload(self):
    self.run_loop.cancel()


def setup(bot):
  bot.add_cog(RichActivity(bot))
