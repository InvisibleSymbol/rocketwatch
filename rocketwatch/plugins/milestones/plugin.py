import datetime
import json
import logging

from discord.ext import commands, tasks
from tinydb import TinyDB, Query
from web3.datastructures import MutableAttributeDict as aDict

from utils import solidity
from utils.cfg import cfg
from utils.embeds import CustomEmbeds
from utils.reporter import report_error
from utils.rocketpool import rp

log = logging.getLogger("milestones")
log.setLevel(cfg["log_level"])


class Milestones(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.state = "OK"
    self.state = {}
    self.db = TinyDB('./plugins/milestones/state.db',
                     create_dirs=True,
                     sort_keys=True,
                     indent=4,
                     separators=(',', ': '))

    self.embed = CustomEmbeds()

    with open("./plugins/milestones/milestones.json") as f:
      self.milestones = json.load(f)

    if not self.run_loop.is_running():
      self.run_loop.start()

  @tasks.loop(seconds=60.0)
  async def run_loop(self):
    if self.state == "STOPPED":
      return

    if self.state != "ERROR":
      try:
        self.state = "OK"
        return await self.check_for_new_events()
      except Exception as err:
        self.state = "ERROR"
        await report_error(err)
    try:
      return self.__init__(self.bot)
    except Exception as err:
      log.exception(err)

  # noinspection PyTypeChecker
  async def check_for_new_events(self):
    log.info("Checking Milestones")

    history = Query()
    for milestone in self.milestones:
      milestone = aDict(milestone)
      state = self.db.search(history.name == milestone.name)

      value = getattr(rp, milestone.function)(*milestone.args)
      if milestone.formatter:
        value = getattr(solidity, milestone.formatter)(value)
      log.debug(f"{milestone.name}:{value}")
      if value < milestone.min:
        continue

      step_size = milestone.step_size
      latest_goal = (value // step_size + 1) * step_size

      if state:
        previous_milestone = state[0]["current_goal"]
      else:
        log.debug(f"First time we have processed Milestones for milestone {milestone.name}. Adding it to the Database.")
        self.db.insert({
          "name": milestone.name,
          "current_goal": latest_goal
        })
        previous_milestone = milestone.min
      if previous_milestone < latest_goal:
        log.info(f"Goal for milestone {milestone.name} has increased. Triggering Milestone!")
        embed = self.embed.assemble(aDict({
          "timestamp": int(datetime.datetime.now().timestamp()),
          "event_name": milestone.name,
          "milestone_value": previous_milestone,
          "result_value": value
        }))
        default_channel = await self.bot.fetch_channel(cfg["discord.channels.default"])
        await default_channel.send(embed=embed)
        self.db.upsert({
          "name": milestone.name,
          "current_goal": latest_goal
        },
          history.name == milestone.name)

    log.debug("Finished Checking Milestones")

  def cog_unload(self):
    self.state = "STOPPED"
    self.run_loop.cancel()


def setup(bot):
  bot.add_cog(Milestones(bot))
