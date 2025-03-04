import logging

import cronitor
from discord import Activity, ActivityType
from discord.ext import commands, tasks

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.rocketpool import rp

log = logging.getLogger("rich_activity")
log.setLevel(cfg["log_level"])

class RichActivity(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.monitor = cronitor.Monitor('update-activity', api_key=cfg["cronitor_secret"])

        if not self.run_loop.is_running() and bot.is_ready():
            self.run_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.run_loop.is_running():
            self.run_loop.start()

    @tasks.loop(seconds=60.0)
    async def run_loop(self):
        self.monitor.ping()
        try:
            log.debug("Updating Discord activity")
            mp_count = rp.call("rocketMinipoolManager.getMinipoolCount")
            await self.bot.change_presence(
                activity=Activity(
                    type=ActivityType.watching,
                    name=f"{mp_count:,} minipools!"
                )
            )
        except Exception as err:
            await self.bot.report_error(err)

    def cog_unload(self):
        self.run_loop.cancel()


async def setup(bot):
    await bot.add_cog(RichActivity(bot))
