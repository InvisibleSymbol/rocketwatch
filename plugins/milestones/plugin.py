import datetime
import json
import logging
import os

from discord.ext import commands, tasks
from tinydb import TinyDB, Query
from web3 import Web3
from web3.datastructures import MutableAttributeDict as aDict

import utils.embeds
from utils.rocketpool import RocketPool

log = logging.getLogger("milestones")
log.setLevel(os.getenv("LOG_LEVEL"))


class Milestones(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.loaded = True
    self.state = {}
    self.db = TinyDB('./plugins/milestones/state.db',
                     create_dirs=True,
                     sort_keys=True,
                     indent=4,
                     separators=(',', ': '))

    infura_id = os.getenv("INFURA_ID")
    self.w3 = Web3(Web3.WebsocketProvider(f"wss://goerli.infura.io/ws/v3/{infura_id}"))
    self.rocketpool = RocketPool(self.w3,
                                 os.getenv("STORAGE_CONTRACT"))

    with open("./plugins/milestones/milestones.json") as f:
      self.milestones = json.load(f)

    if not self.run_loop.is_running():
      self.run_loop.start()

  @tasks.loop(seconds=60.0)
  async def run_loop(self):
    if self.loaded:
      try:
        return await self.check_for_new_events()
      except Exception as err:
        self.loaded = False
        log.exception(err)
    try:
      return self.__init__(self.bot)
    except Exception as err:
      self.loaded = False
      log.exception(err)

  # noinspection PyTypeChecker
  async def check_for_new_events(self):
    if not self.loaded:
      return
    log.info("Checking Milestones")

    history = Query()
    for function, args in self.milestones.items():
      args = aDict(args)
      state = self.db.search(history.function == function)

      value = getattr(self.rocketpool, function)()
      if value < args.min:
        continue

      step_size = args.step_size
      latest_goal = (value // step_size + 1) * step_size

      if state:
        previous_milestone = state[0]["current_goal"]
      else:
        log.debug(f"First time we have processed Milestones for function {function}. Adding it to the Database.")
        self.db.insert({
          "function": function,
          "current_goal": latest_goal
        })
        previous_milestone = args.min
      if previous_milestone < latest_goal:
        log.info(f"Goal for function {function} has increased. Triggering Milestone!")
        embed = utils.embeds.assemble(aDict({
          "timestamp": int(datetime.datetime.now().timestamp()),
          "event_name": f"{function}_milestone",
          "milestone_value": previous_milestone,
          "result_value": value
        }))
        default_channel = await self.bot.fetch_channel(os.getenv("OUTPUT_CHANNEL_DEFAULT"))
        await default_channel.send(embed=embed)
        self.db.upsert({
          "function": function,
          "current_goal": latest_goal
        },
          history.function == function)

    log.debug("Finished Checking Milestones")

  def cog_unload(self):
    self.loaded = False
    self.run_loop.cancel()


def setup(bot):
  bot.add_cog(Milestones(bot))
