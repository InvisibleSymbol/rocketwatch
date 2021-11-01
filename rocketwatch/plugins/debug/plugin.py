import io
import json
import random

from discord import File
from discord.ext import commands

from utils.cfg import cfg
from utils.readable import etherscan_url, prettify_json_string
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
    async def call(self, ctx, command, json_args):
        await ctx.send(f"`{command}: {rp.call(command, *json.loads(json_args))}`", hidden=True)

    @owner_only_slash()
    async def get_abi_from_contract(self, ctx, contract):
        with io.StringIO(prettify_json_string(rp.uncached_get_abi_by_name(contract))) as f:
            await ctx.send(file=File(fp=f, filename=f"{contract}.{cfg['rocketpool.chain']}.abi.json"))

    @owner_only_slash()
    async def get_address_of_contract(self, ctx, contract):
        await ctx.send(etherscan_url(rp.uncached_get_address_by_name(contract)))

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
