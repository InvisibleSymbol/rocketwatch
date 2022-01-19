import io
import json
import random
from pathlib import Path

import humanize
from discord import File, AutocompleteContext
from discord.commands import slash_command, Option
from discord.ext import commands

from utils import solidity
from utils.cfg import cfg
from utils.embeds import etherscan_url
from utils.get_nearest_block import get_block_by_timestamp
from utils.readable import prettify_json_string
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.slash_permissions import owner_only_slash

# generate list of all file names with the .sol extension from the rocketpool submodule
contract_files = []
for path in Path("contracts/rocketpool/contracts/contract").glob('**/*.sol'):
    # append to list but ensure that the first character is lowercase
    file_name = path.stem
    contract_files.append(file_name[0].lower() + file_name[1:])


async def match_contract_names(ctx: AutocompleteContext):
    return [name for name in contract_files if ctx.value.lower() in name.lower()]


async def match_function_name(ctx: AutocompleteContext):
    # return nothing if contract name hasn't been specified in the options yet
    if "contract" not in ctx.options:
        return []
    # get the contract name from the options
    contract_name = ctx.options["contract"]
    contract = rp.get_contract_by_name(contract_name)
    return [function_name for function_name in contract.functions if ctx.value.lower() in function_name.lower()]


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
                   contract: Option(
                       str,
                       autocomplete=match_contract_names,
                       required=True),
                   function: Option(
                       str,
                       autocomplete=match_function_name,
                       required=True),
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
        command = f"{contract}.{function}"

        try:
            args = json.loads(json_args)
            if not isinstance(args, list):
                args = [args]
            v = rp.call(command, *args, block=block)
        except Exception as err:
            await ctx.respond(f"Exception: ```{repr(err)}```")
            return
        try:
            g = rp.estimate_gas_for_call(command, *args, block=block)
        except Exception as err:
            g = "N/A"
            if isinstance(err, ValueError) and err.args[0]["code"] == -32000:
                g += f" ({err.args[0]['message']})"

        if isinstance(v, int) and abs(v) >= 10 ** 12:
            v = solidity.to_float(v)
        g = humanize.intcomma(g)

        await ctx.respond(f"`block: {block}`\n`gas estimate: {g}`\n`{command}({', '.join([repr(a) for a in args])}): {v}`")

    @slash_command(guild_ids=guilds)
    async def get_abi_of_contract(self, ctx, contract: Option(str, autocomplete=match_contract_names)):
        """retrieves the latest ABI for a contract"""
        await ctx.defer()
        try:
            with io.StringIO(prettify_json_string(rp.uncached_get_abi_by_name(contract))) as f:
                await ctx.respond(file=File(fp=f, filename=f"{contract}.{cfg['rocketpool.chain']}.abi.json"))
        except Exception as err:
            await ctx.respond(f"Exception: ```{repr(err)}```")
            return

    @slash_command(guild_ids=guilds)
    async def get_address_of_contract(self, ctx, contract: Option(str, autocomplete=match_contract_names)):
        """retrieves the latest address for a contract"""
        await ctx.defer()
        try:
            await ctx.respond(etherscan_url(rp.uncached_get_address_by_name(contract)))
        except Exception as err:
            await ctx.respond(f"Exception: ```{repr(err)}```")
            return

    @owner_only_slash()
    async def delete(self,
                     ctx,
                     message):
        channel_id, message_id = message.split("/")[-2:]
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

    @owner_only_slash()
    async def debug_transaction(self, ctx, tnx_hash):
        transaction_receipt = w3.eth.getTransaction(tnx_hash)
        revert_reason = rp.get_revert_reason(transaction_receipt)
        await ctx.respond(f"```Revert Reason: {revert_reason}```", ephemeral=True)

    @slash_command(guild_ids=guilds)
    async def get_block_by_timestamp(self, ctx, timestamp:int):
        await ctx.defer()
        block, steps = get_block_by_timestamp(timestamp)
        found_timestamp = w3.eth.get_block(block).timestamp
        if found_timestamp == timestamp:
            await ctx.respond(f"```Found perfect match for timestamp: {timestamp}\nBlock: {block}\nSteps took: {steps}```", ephemeral=True)
        else:
            await ctx.respond(f"```Found closest match for timestamp: {timestamp}\nFound: {found_timestamp}\nBlock: {block}\nSteps took: {steps}```", ephemeral=True)


def setup(bot):
    bot.add_cog(Debug(bot))
