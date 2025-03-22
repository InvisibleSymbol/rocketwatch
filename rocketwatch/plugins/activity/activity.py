import logging

from cronitor import Monitor
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
        self.monitor = Monitor("update-activity", api_key=cfg["other.secrets.cronitor"])
        self.loop.start()

    def cog_unload(self):
        self.loop.cancel()

    @tasks.loop(seconds=60)
    async def loop(self):
        self.monitor.ping()
        log.debug("Updating Discord activity")
        
        minipool_count = rp.call("rocketMinipoolManager.getActiveMinipoolCount")
        await self.bot.change_presence(
            activity=Activity(
                type=ActivityType.watching,
                name=f"{minipool_count:,} minipools"
            )
        )

    @loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
        
    @loop.error
    async def on_error(self, err: Exception):
        await self.bot.report_error(err)


async def setup(bot):
    await bot.add_cog(RichActivity(bot))
