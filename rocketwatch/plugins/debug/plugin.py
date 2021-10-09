import random

from discord.ext import commands
from web3 import Web3, WebsocketProvider

from utils.cfg import cfg
from utils.rocketpool import RocketPool
from utils.slash_permissions import owner_only_slash


class Debug(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.w3 = Web3(WebsocketProvider(f"wss://{cfg['rocketpool.chain']}.infura.io/ws/v3/{cfg['rocketpool.infura_secret']}"))
    self.rp = RocketPool(self.w3)

  @owner_only_slash()
  async def raise_exception(self, ctx):
    with open(str(random.random()), "rb"):
      raise Exception("this should never happen wtf is your filesystem")

  @owner_only_slash()
  async def call(self, ctx, command):
    await ctx.send(f"`{command}: {self.rp.call(command)}`", hidden=True)

  @owner_only_slash()
  async def decode_tnx(self, ctx, tnx_hash, contract_name=None):
    tnx = self.w3.eth.get_transaction(tnx_hash)
    if contract_name:
      contract = self.rp.get_contract_by_name(contract_name)
    else:
      contract = self.rp.get_contract_by_address(tnx.to)
    data = contract.decode_function_input(tnx.input)
    await ctx.send(f"```Input:\n{data}```", hidden=True)


def setup(bot):
  bot.add_cog(Debug(bot))
