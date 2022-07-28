import asyncio
import io
import requests
import json
import logging
import random
import time
from pathlib import Path

import humanize
from checksumdir import dirhash
from colorama import Fore, Style
from discord import File, Object
from discord.app_commands import Choice, guilds, describe
from discord.ext.commands import is_owner, Cog, Bot, hybrid_command, Context
from motor.motor_asyncio import AsyncIOMotorClient
from web3.datastructures import MutableAttributeDict as aDict

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import el_explorer_url, Embed
from utils.get_nearest_block import get_block_by_timestamp
from utils.get_or_fetch import get_or_fetch_channel
from utils.readable import prettify_json_string
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.visibility import is_hidden

log = logging.getLogger("debug")
log.setLevel(cfg["log_level"])


class Debug(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.ran = False
        self.contract_files = []
        self.function_list = []

    # --------- LISTENERS --------- #

    @Cog.listener()
    async def on_ready(self):
        if self.ran:
            return
        self.ran = True
        log.info("Checking if plugins have changed!")
        plugins_hash = dirhash("plugins")
        log.debug(f"Plugin folder hash: {plugins_hash}")
        # check if hash in db matches
        db_entry = await self.db.state.find_one({"_id": "plugins_hash"})
        if db_entry and plugins_hash == db_entry.get("hash"):
            log.info("Plugins have not changed!")
            return
        log.info("Plugins have changed! Updating Commands...")
        await self.bot.tree.sync()
        await self.bot.tree.sync(guild=Object(id=cfg["discord.owner.server_id"]))
        await self.db.state.update_one({"_id": "plugins_hash"}, {"$set": {"hash": plugins_hash}}, upsert=True)
        log.info("Commands updated!")
        log.info("Indexing Rocket Pool contracts...")
        # generate list of all file names with the .sol extension from the rocketpool submodule
        for path in Path("contracts/rocketpool/contracts/contract").glob('**/*.sol'):
            # append to list but ensure that the first character is lowercase
            file_name = path.stem
            contract = file_name[0].lower() + file_name[1:]
            self.contract_files.append(contract)
            try:
                rp.get_contract_by_name(contract)
            except Exception as e:
                log.warning(f"Skipping {contract} in function list generation: {e}")
                continue
            for function in rp.get_contract_by_name(contract).functions:
                self.function_list.append(f"{contract}.{function}")
            await asyncio.sleep(0.1)
        self.contract_files.extend(list(cfg["rocketpool.manual_addresses"].keys()))
        log.info("Done!")

    # --------- PRIVATE OWNER COMMANDS --------- #

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def raise_exception(self, ctx: Context):
        """
        Raise an exception for testing purposes.
        """
        with open(str(random.random()), "rb"):
            raise Exception("this should never happen wtf is your filesystem")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def delete(self, ctx: Context, message_url: str):
        """
        Guess what. It deletes a message.
        """
        await ctx.defer(ephemeral=True)
        channel_id, message_id = message_url.split("/")[-2:]
        channel = await get_or_fetch_channel(self.bot, channel_id)
        msg = await channel.fetch_message(message_id)
        await msg.delete()
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def decode_tnx(self, ctx: Context, tnx_hash: str, contract_name: str = None):
        """
        Decode transaction calldata
        """
        await ctx.defer(ephemeral=True)
        tnx = w3.eth.get_transaction(tnx_hash)
        if contract_name:
            contract = rp.get_contract_by_name(contract_name)
        else:
            contract = rp.get_contract_by_address(tnx.to)
        data = contract.decode_function_input(tnx.input)
        await ctx.send(content=f"```Input:\n{data}```")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def debug_transaction(self, ctx: Context, tnx_hash: str):
        """
        Try to return the revert reason of a transaction.
        """
        await ctx.defer(ephemeral=True)
        transaction_receipt = w3.eth.getTransaction(tnx_hash)
        if revert_reason := rp.get_revert_reason(transaction_receipt):
            await ctx.send(content=f"```Revert reason: {revert_reason}```")
        else:
            await ctx.send(content="```No revert reason Available```")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def purge_minipools(self, ctx: Context, confirm: bool = False):
        """
        Purge minipool collection, so it can be resynced from scratch in the next update.
        """
        await ctx.defer(ephemeral=True)
        if not confirm:
            await ctx.send("Not running. Set `confirm` to `true` to run.")
            return
        await self.db.minipools.drop()
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def tmp_fix(self, ctx: Context):
        await ctx.defer(ephemeral=True)
        # delete slot index of proposal collection
        await self.db.proposals.drop_index("slot_1")
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def overwrite_events_block(self, ctx: Context, block_number: int):
        """
        Overwrite the events block number in the latest_checked_block collection.
        """
        await ctx.defer(ephemeral=True)
        await self.db.last_checked_block.update_one({"_id": "events"}, {"$set": {"block": block_number}})
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def full_sync_commands(self, ctx: Context):
        """
        Full sync of the commands tree.
        """
        await ctx.defer(ephemeral=True)
        await self.bot.tree.sync()
        for guild in [cfg["discord.owner.server_id"], *cfg["discord.guilds"]]:
            await self.bot.tree.sync(guild=Object(id=guild))
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def fix_fuckup_1(self, ctx: Context,
                           message_id: str):
        """
        Fix fuckup #1: incorrect format specifier in slashing message.
        """
        await ctx.defer(ephemeral=True)
        event_id = "1656176303:slash-391311:slasher-347354:slashing-type-Attestation"
        event = await self.db.event_queue.find_one({"_id": event_id, "processed": True})
        if not event:
            await ctx.send(content="Event not found.")
            return
        e = Response.get_embed(event)
        e.description = e.description.replace("%{minipool_clean}", "[391311](https://beaconcha.in/validator/391311)")
        channel = await get_or_fetch_channel(self.bot, event["channel_id"])
        msg = await channel.fetch_message(message_id)
        await msg.edit(embed=e)
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def fix_fuckup_2(self, ctx: Context,
                            message_id: str,
                            transaction_id: str):
        """
        Fix fuckup #2: missing translations for queue clear events.
        """
        await ctx.defer(ephemeral=True)
        cog = self.bot.get_cog("QueuedTransactions")
        tnx = w3.eth.get_transaction(transaction_id)
        contract = rp.get_contract_by_address(tnx.to)
        decoded = contract.decode_function_input(tnx.input)
        function = decoded[0].function_identifier

        event = aDict(tnx)
        event.args = {}
        for arg, value in decoded[1].items():
            event.args[arg.lstrip("_")] = value
        block = w3.eth.getBlock(tnx.blockNumber)
        event.args["timestamp"] = block.timestamp
        event.args["function_name"] = function
        event_name = "deposit_pool_queue_clear_partial"
        embed = cog.create_embed(event_name, event)
        channel = await get_or_fetch_channel(self.bot, cfg["discord.channels.default"])
        msg = await channel.fetch_message(message_id)
        await msg.edit(embed=embed)
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def talk(self, ctx: Context, channel: str, message: str):
        """
        Send a message to a channel.
        """
        await ctx.defer(ephemeral=True)
        channel = await get_or_fetch_channel(self.bot, channel)
        await channel.send(message)
        await ctx.send(content="Done", ephemeral=True)

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def announce(self, ctx: Context, channel: str, message: str):
        """
        Send a message to a channel.
        """
        await ctx.defer(ephemeral=True)
        channel = await get_or_fetch_channel(self.bot, channel)
        e = Embed(title="Announcement", description=message)
        e.add_field(name="Timestamp", value=f"<t:{int(time.time())}:R> (<t:{int(time.time())}:f>)")
        await channel.send(embed=e)
        await ctx.send(content="Done", ephemeral=True)

    # --------- PUBLIC COMMANDS --------- #

    @hybrid_command()
    async def color_test(self, ctx: Context):
        """
        Simple test to check ansi color support
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        payload = "```ansi"
        for fg_name, fg in Fore.__dict__.items():
            if fg_name.endswith("_EX"):
                continue
            payload += f"\n{fg}Hello World"
        payload += f"{Style.RESET_ALL}```"
        await ctx.reply(content=payload)

    # --------- PUBLIC COMMANDS --------- #

    @hybrid_command()
    async def asian_restaurant_name(self, ctx: Context):
        """
        Randomly generated Asian Restaurant Names.
        """
        hidden = ctx.channel.name not in ["random", "rocket-watch", "trading"]
        await ctx.defer(ephemeral=hidden)
        a = requests.get("https://www.dotomator.com/api/random_name.json?type=asian").json()["name"]
        await ctx.reply(a)

    @hybrid_command()
    async def get_block_by_timestamp(self, ctx: Context, timestamp: int):
        """
        Get a block using a timestamp. Useful for contracts that track blocktime instead of blocknumber.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        block, steps = get_block_by_timestamp(timestamp)
        found_timestamp = w3.eth.get_block(block).timestamp
        if found_timestamp == timestamp:
            text = f"```Found perfect match for timestamp: {timestamp}\n" \
                   f"Block: {block}\n" \
                   f"Steps taken: {steps}```"
        else:
            text = f"```Found closest match for timestamp: {timestamp}\n" \
                   f"Timestamp: {found_timestamp}\n" \
                   f"Block: {block}\n" \
                   f"Steps taken: {steps}```"
        await ctx.send(content=text)

    @hybrid_command()
    async def get_abi_of_contract(self, ctx: Context, contract: str):
        """retrieve the latest ABI for a contract"""
        await ctx.defer()
        try:
            with io.StringIO(prettify_json_string(rp.uncached_get_abi_by_name(contract))) as f:
                await ctx.send(
                    files=[File(fp=f, filename=f"{contract}.{cfg['rocketpool.chain']}.abi.json")])
        except Exception as err:
            await ctx.send(content=f"```Exception: {repr(err)}```")

    @hybrid_command()
    async def get_address_of_contract(self, ctx: Context, contract: str):
        """retrieve the latest address for a contract"""
        await ctx.defer()
        try:
            address = cfg["rocketpool.manual_addresses"].get(contract)
            if not address:
                address = rp.uncached_get_address_by_name(contract)
            await ctx.send(content=el_explorer_url(address))
        except Exception as err:
            await ctx.send(content=f"Exception: ```{repr(err)}```")

    @hybrid_command()
    @describe(json_args="json formatted arguments. example: `[1, \"World\"]`",
              block="call against block state")
    async def call(self,
                   ctx: Context,
                   function: str,
                   json_args: str = "[]",
                   block: str = "latest"):
        """Call Function of Contract"""
        await ctx.defer()

        try:
            args = json.loads(json_args)
            if not isinstance(args, list):
                args = [args]
            v = rp.call(function, *args, block=block)
        except Exception as err:
            await ctx.send(content=f"Exception: ```{repr(err)}```")
            return
        try:
            g = rp.estimate_gas_for_call(function, *args, block=block)
        except Exception as err:
            g = "N/A"
            if isinstance(err, ValueError) and err.args[0]["code"] == -32000:
                g += f" ({err.args[0]['message']})"

        if isinstance(v, int) and abs(v) >= 10 ** 12:
            v = solidity.to_float(v)
        g = humanize.intcomma(g)

        await ctx.send(
            content=f"`block: {block}`\n`gas estimate: {g}`\n`{function}({', '.join([repr(a) for a in args])}): {v}`")

    # --------- OTHERS --------- #

    @get_address_of_contract.autocomplete("contract")
    @get_abi_of_contract.autocomplete("contract")
    @decode_tnx.autocomplete("contract_name")
    async def match_contract_names(self, ctx: Context, current: str):
        return [Choice(name=name, value=name) for name in self.contract_files if current.lower() in name.lower()][:25]

    @call.autocomplete("function")
    async def match_function_name(self, ctx: Context, current: str):
        return [Choice(name=name, value=name) for name in self.function_list if current.lower() in name.lower()][:25]


async def setup(bot):
    await bot.add_cog(Debug(bot))
