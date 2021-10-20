import random
import json
import io

from discord.ext import commands
from discord import File

from utils.cfg import cfg
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import owner_only_slash


class Debug(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

  @owner_only_slash()
  async def raise_exception(self, ctx):
    with open(str(random.random()), "rb"):
      raise Exception("this should never happen wtf is your filesystem")

  @owner_only_slash()
  async def call(self, ctx, command):
    await ctx.send(f"`{command}: {rp.call(command)}`", hidden=True)

  @owner_only_slash()
  async def get_abi_from_contract(self, ctx, contract):
    abi = json.loads(rp.get_abi_by_name(contract))
    with io.StringIO(json.dumps(abi, indent=4)) as f:
      await ctx.send(file=File(fp=f, filename=f"{contract}.{cfg['rocketpool.chain']}.abi.json"))

  @owner_only_slash()
  async def decode_tnx(self, ctx, tnx_hash, contract_name=None):
    tnx = w3.eth.get_transaction(tnx_hash)
    if contract_name:
      contract = rp.get_contract_by_name(contract_name)
    else:
      contract = rp.get_contract_by_address(tnx.to)
    data = contract.decode_function_input(tnx.input)
    await ctx.send(f"```Input:\n{data}```", hidden=True)


def setup(bot):
  bot.add_cog(Debug(bot))
