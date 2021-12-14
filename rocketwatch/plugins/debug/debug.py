import io
import json
import random

import humanize
from discord import File
from discord.commands import slash_command, Option
from discord.ext import commands

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

    @slash_command(guild_ids=guilds)
    async def call(self,
                   ctx,
                   command: Option(
                       str,
                       "Syntax: `contractName.functionName`. Example: `rocketTokenRPL.totalSupply`"),
                   json_args: Option(
                       str,
                       "json formatted arguments. example: `[1, \"World\"]`",
                       default="[]",
                       required=False),
                   block: Option(
                       int,
                       "call against block state",
                       default="latest",
                       required=False)):
        """Call Function of Contract"""
        await ctx.defer()

        try:
            args = json.loads(json_args)
            if not isinstance(args, list):
                args = [args]
            v = rp.call(command, *args, block=block)
            g = rp.estimate_gas_for_call(command, *args, block=block)
        except Exception as err:
            await ctx.respond(f"Exception: ```{repr(err)}```")
            return

        if isinstance(v, int) and abs(v) >= 10 ** 12:
            v = solidity.to_float(v)
        g = humanize.intcomma(g)

        await ctx.respond(f"`block: {block}`\n`gas estimate: {g}`\n`{command}({', '.join([repr(a) for a in args])}): {v}`")

    @slash_command(guild_ids=guilds)
    async def get_abi_from_contract(self, ctx, contract):
        """retrieves the latest ABI for a contract"""
        await ctx.defer()
        try:
            with io.StringIO(prettify_json_string(rp.uncached_get_abi_by_name(contract))) as f:
                await ctx.respond(file=File(fp=f, filename=f"{contract}.{cfg['rocketpool.chain']}.abi.json"))
        except Exception as err:
            await ctx.respond(f"Exception: ```{repr(err)}```")
            return

    @slash_command(guild_ids=guilds)
    async def get_address_of_contract(self, ctx, contract):
        """retrieves the latest address for a contract"""
        await ctx.defer()
        try:
            await ctx.respond(etherscan_url(rp.uncached_get_address_by_name(contract)))
        except Exception as err:
            await ctx.respond(f"Exception: ```{repr(err)}```")
            return

    @owner_only_slash()
    async def delete(self, ctx, channel_id, message_id):
        channel = await self.bot.fetch_channel(channel_id)
        msg = await channel.fetch_message(message_id)
        await msg.delete()
        await ctx.respond("Done", ephemeral=True)

    @owner_only_slash()
    async def decode_tnx(self, ctx, tnx_hash, contract_name=None):
        tnx = w3.eth.get_transaction(tnx_hash)
        if contract_name:
            contract = rp.get_contract_by_name(contract_name)
        else:
            contract = rp.get_contract_by_address(tnx.to)
        data = contract.decode_function_input(tnx.input)
        await ctx.respond(f"```Input:\n{data}```", ephemeral=True)


def setup(bot):
    bot.add_cog(Debug(bot))
