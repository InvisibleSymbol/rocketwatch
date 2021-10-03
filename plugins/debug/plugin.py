import os
import random

import psutil
from discord.ext import commands
from web3 import Web3

from utils.rocketpool import RocketPool
from utils.slash_permissions import owner_only_slash

psutil.getloadavg()


class Debug(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.process = psutil.Process(os.getpid())
    infura_id = os.getenv("INFURA_ID")
    self.w3 = Web3(Web3.WebsocketProvider(f"wss://mainnet.infura.io/ws/v3/{infura_id}"))
    self.rp = RocketPool(self.w3,
                         os.getenv("STORAGE_CONTRACT"))

  @owner_only_slash()
  async def raise_exception(self, ctx):
    with open(str(random.random()), "rb"):
      raise Exception("this should never happen wtf is your filesystem")

  @owner_only_slash()
  async def call(self, ctx, command):
    await ctx.send(f"`{command}: {self.rp.call(command)}`", hidden=True)


def setup(bot):
  bot.add_cog(Debug(bot))
