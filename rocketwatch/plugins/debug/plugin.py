import os
import random

from discord.ext import commands
from eth_typing import HexStr
from web3 import Web3

from utils.rocketpool import RocketPool
from utils.slash_permissions import owner_only_slash


class Debug(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
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
