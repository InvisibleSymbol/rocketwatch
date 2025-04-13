import io
import json
import logging
import random
import time

import humanize
import requests
from colorama import Fore, Style
from discord import File, Object
from discord.app_commands import Choice, guilds, describe
from discord.ext.commands import is_owner, Cog, hybrid_command, Context
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import el_explorer_url, Embed
from utils.get_nearest_block import get_block_by_timestamp
from utils.readable import prettify_json_string
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.visibility import is_hidden, is_hidden_weak, is_hidden_role_controlled

log = logging.getLogger("debug")
log.setLevel(cfg["log_level"])


class Debug(Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb.uri"]).rocketwatch
        self.contract_names = []
        self.function_names = []

    # --------- LISTENERS --------- #

    @Cog.listener()
    async def on_ready(self):
        if self.function_names:
            return

        for contract in rp.addresses.copy():
            try:
                for function in rp.get_contract_by_name(contract).functions:
                    self.function_names.append(f"{contract}.{function}")
                self.contract_names.append(contract)
            except Exception:
                log.exception(f"Could not get function list for {contract}")

    # --------- PRIVATE OWNER COMMANDS --------- #

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def raise_exception(self, _: Context):
        """
        Raise an exception for testing purposes.
        """
        with open(str(random.random()), "rb"):
            raise Exception("this should never happen wtf is your filesystem")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def get_members_of_role(self, ctx: Context, guild_id: str, role_id: str):
        """Get members of a role"""
        await ctx.defer(ephemeral=True)
        try:
            guild = self.bot.get_guild(int(guild_id))
            log.debug(guild)
            role = guild.get_role(int(role_id))
            log.debug(role)
            # print name + identifier and id of each member
            members = [f"{member.name}#{member.discriminator}, ({member.id})" for member in role.members]
            # generate a file with a header that mentions what role and guild the members are from
            content = f"Members of {role.name} ({role.id}) in {guild.name} ({guild.id})\n\n" + "\n".join(members)
            file = File(io.BytesIO(content.encode()), "members.txt")
            await ctx.send(file=file)
        except Exception as err:
            await ctx.send(content=f"```{repr(err)}```")

    # list all roles of a guild with name and id
    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def get_roles(self, ctx: Context, guild_id: str):
        """Get roles of a guild"""
        await ctx.defer(ephemeral=True)
        try:
            guild = self.bot.get_guild(int(guild_id))
            log.debug(guild)
            # print name + identifier and id of each member
            roles = [f"{role.name}, ({role.id})" for role in guild.roles]
            # generate a file with a header that mentions what role and guild the members are from
            content = f"Roles of {guild.name} ({guild.id})\n\n" + "\n".join(roles)
            file = File(io.BytesIO(content.encode()), filename="roles.txt")
            await ctx.send(file=file)
        except Exception as err:
            await ctx.send(content=f"```{repr(err)}```")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def delete(self, ctx: Context, message_url: str):
        """
        Guess what. It deletes a message.
        """
        await ctx.defer(ephemeral=True)
        channel_id, message_id = message_url.split("/")[-2:]
        channel = await self.bot.get_or_fetch_channel(int(channel_id))
        msg = await channel.fetch_message(int(message_id))
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
    async def purge_minipools_new(self, ctx: Context, confirm: bool = False):
        """
        Purge minipools_new collection, so it can be resynced from scratch in the next update.
        """
        await ctx.defer(ephemeral=True)
        if not confirm:
            await ctx.send("Not running. Set `confirm` to `true` to run.")
            return
        await self.db.minipools_new.drop()
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def sync_commands(self, ctx: Context):
        """
        Full sync of the commands tree
        """
        await ctx.defer(ephemeral=True)
        await self.bot.sync_commands()
        await ctx.send(content="Done")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def talk(self, ctx: Context, channel: str, message: str):
        """
        Send a message to a channel.
        """
        await ctx.defer(ephemeral=True)
        channel = await self.bot.get_or_fetch_channel(int(channel))
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
        channel = await self.bot.get_or_fetch_channel(int(channel))
        e = Embed(title="Announcement", description=message)
        e.add_field(name="Timestamp", value=f"<t:{int(time.time())}:R> (<t:{int(time.time())}:f>)")
        await channel.send(embed=e)
        await ctx.send(content="Done", ephemeral=True)

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def restore_missed_events(self, ctx: Context, tx_hash: str):
        import pickle
        from datetime import datetime
        from plugins.events.events import Events

        await ctx.defer(ephemeral=True)

        events_plugin: Events = self.bot.cogs["Events"]

        filtered_events = []
        for event_log in w3.eth.get_transaction_receipt(tx_hash).logs:
            if ("topics" in event_log) and (event_log["topics"][0].hex() in events_plugin.topic_map):
                filtered_events.append(event_log)

        channels = cfg["discord.channels"]
        events, _ = events_plugin.process_events(filtered_events)
        for event in events:
            channel_candidates = [value for key, value in channels.items() if event.event_name.startswith(key)]
            channel_id = channel_candidates[0] if channel_candidates else channels["default"]
            await self.db.event_queue.insert_one({
                "_id": event.unique_id,
                "embed": pickle.dumps(event.embed),
                "topic": event.topic,
                "event_name": event.event_name,
                "block_number": event.block_number,
                "score": event.get_score(),
                "time_seen": datetime.now(),
                "attachment": pickle.dumps(event.attachment) if event.attachment else None,
                "channel_id": channel_id,
                "message_id": None
            })
            await ctx.send(embed=event.embed, ephemeral=True)
        await ctx.send(content="Done", ephemeral=True)
        
    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def fix_recurring_spend_claim_event(self, ctx: Context, message_id: str):
        await ctx.defer(ephemeral=True)
        
        from plugins.events.events import Events
        
        event_channel = await self.bot.fetch_channel(cfg["discord.channels.dao"])
        message = await event_channel.fetch_message(int(message_id))
        fields = {field.name: field.value for field in message.embeds[0].fields} 
        tx_link = fields["Transaction Hash"].split(" ")[-1]
        tx_hash = tx_link.split("/tx/")[1].split(")")[0]
                
        receipt = w3.eth.get_transaction_receipt(tx_hash)        
        tx_plugin: Events = self.bot.cogs["Events"]
        
        logs = []
        for event_log in receipt.logs:
            if ("topics" in event_log) and (event_log["topics"][0].hex() in tx_plugin.topic_map):
                logs.append(event_log)

        responses, _ = tx_plugin.process_events(logs)
        await message.edit(embed=responses[-1].embed)
        await ctx.send(content="Done.")

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

    @hybrid_command()
    async def asian_restaurant_name(self, ctx: Context):
        """
        Randomly generated Asian Restaurant Names.
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
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
        await ctx.defer(ephemeral=is_hidden_role_controlled(ctx.interaction))
        try:
            abi = prettify_json_string(rp.uncached_get_abi_by_name(contract))
            await ctx.send(file=File(
                fp=io.BytesIO(abi.encode()),
                filename=f"{contract}.{cfg['rocketpool.chain']}.abi.json")
            )
        except Exception as err:
            await ctx.send(content=f"```Exception: {repr(err)}```")

    @hybrid_command()
    async def get_address_of_contract(self, ctx: Context, contract: str):
        """retrieve the latest address for a contract"""
        await ctx.defer(ephemeral=is_hidden_role_controlled(ctx.interaction))
        try:
            address = cfg["rocketpool.manual_addresses"].get(contract)
            if not address:
                address = rp.uncached_get_address_by_name(contract)
            await ctx.send(content=el_explorer_url(address))
        except Exception as err:
            await ctx.send(content=f"Exception: ```{repr(err)}```")
            if "No address found for" in repr(err):
                # private response as a tip
                m = "It may be that you are requesting the address of a contract that does not get deployed (`rocketBase` for example), " \
                    " is deployed multiple times (i.e node operator related contracts, like `rocketNodeDistributor`)," \
                    " or is not yet deployed on the current chain.\n... Or you simply messed up the name :P"
                await ctx.send(content=m, ephemeral=True)

    @hybrid_command()
    @describe(json_args="json formatted arguments. example: `[1, \"World\"]`",
              block="call against block state")
    async def call(self,
                   ctx: Context,
                   function: str,
                   json_args: str = "[]",
                   block: str = "latest",
                   address: str = None,
                   raw_output: bool = False):
        """Call Function of Contract"""
        await ctx.defer(ephemeral=is_hidden_role_controlled(ctx.interaction))
        # convert block to int if number
        if block.isnumeric():
            block = int(block)
        try:
            args = json.loads(json_args)
            if not isinstance(args, list):
                args = [args]
            v = rp.call(function, *args, block=block, address=w3.toChecksumAddress(address) if address else None)
        except Exception as err:
            await ctx.send(content=f"Exception: ```{repr(err)}```")
            return
        try:
            g = rp.estimate_gas_for_call(function, *args, block=block)
        except Exception as err:
            g = "N/A"
            if isinstance(err, ValueError) and err.args and "code" in err.args and err.args[0]["code"] == -32000:
                g += f" ({err.args[0]['message']})"

        if isinstance(v, int) and abs(v) >= 10 ** 12 and not raw_output:
            v = solidity.to_float(v)
        g = humanize.intcomma(g)
        text = f"`block: {block}`\n`gas estimate: {g}`\n`{function}({', '.join([repr(a) for a in args])}): "
        if len(text + str(v)) > 2000:
            text += "too long, attached as file`"
            await ctx.send(text, file=File(io.BytesIO(str(v).encode()), "exception.txt"))
        else:
            text += f"{str(v)}`"
            await ctx.send(content=text)

    # --------- OTHERS --------- #

    @get_address_of_contract.autocomplete("contract")
    @get_abi_of_contract.autocomplete("contract")
    @decode_tnx.autocomplete("contract_name")
    async def match_contract_names(self, ctx: Context, current: str) -> list[Choice[str]]:
        return [Choice(name=name, value=name) for name in self.contract_names if current.lower() in name.lower()][:25]

    @call.autocomplete("function")
    async def match_function_name(self, ctx: Context, current: str) -> list[Choice[str]]:
        return [Choice(name=name, value=name) for name in self.function_names if current.lower() in name.lower()][:25]


async def setup(bot):
    await bot.add_cog(Debug(bot))
