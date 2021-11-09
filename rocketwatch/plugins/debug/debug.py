import io
import json
import random

from discord import File
from discord.ext import commands
from discord_slash import cog_ext
from discord_slash.utils.manage_commands import create_option

from utils import solidity
from utils.cfg import cfg
from utils.readable import etherscan_url, prettify_json_string
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.slash_permissions import owner_only_slash


class Debug(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @owner_only_slash()
    async def raise_exception(self, ctx):
        with open(str(random.random()), "rb"):
            raise Exception("this should never happen wtf is your filesystem")

    @cog_ext.cog_slash(guild_ids=guilds,
                       options=[
                           create_option(
                               name="command",
                               description="Syntax: `contractName.functionName`. Example: `rocketTokenRPL.totalSupply`",
                               option_type=3,
                               required=True),
                           create_option(
                               name="json_args",
                               description="json formated arguments. example: `[1, \"World\"]`",
                               option_type=3,
                               required=False)
                       ]
                       )
    async def call(self, ctx, command, json_args="[]", auto_format=True):
        """Call Function of Contract"""
        try:
            v = rp.call(command, *json.loads(json_args))
        except Exception as err:
            await ctx.send(f"Exception: {repr(err)}")
            return

        if auto_format:
            if isinstance(v, int) and v >= 10 ** 12:
                v = solidity.to_float(v)

        await ctx.send(f"`{command}: {v}`")

    @cog_ext.cog_slash(guild_ids=guilds)
    async def get_abi_from_contract(self, ctx, contract):
        """retrieves the latest ABI for a contract"""
        with io.StringIO(prettify_json_string(rp.uncached_get_abi_by_name(contract))) as f:
            await ctx.send(file=File(fp=f, filename=f"{contract}.{cfg['rocketpool.chain']}.abi.json"))

    @cog_ext.cog_slash(guild_ids=guilds)
    async def get_address_of_contract(self, ctx, contract):
        """retrieves the latest address for a contract"""
        await ctx.send(etherscan_url(rp.uncached_get_address_by_name(contract)))

    @owner_only_slash()
    async def delete(self, ctx, channel_id, message_id):
        channel = await self.bot.fetch_channel(channel_id)
        msg = await channel.fetch_message(message_id)
        await msg.delete()
        await ctx.send("Done", hidden=True)

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
