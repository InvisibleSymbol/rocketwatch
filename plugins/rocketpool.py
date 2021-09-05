import base64
import json
import logging
import os

import discord
from discord import Embed
from discord.ext import commands, tasks
from web3 import Web3

from strings import _
from utils.pako import pako_inflate

log = logging.getLogger("rocketpool")
log.setLevel("DEBUG")


class RocketPool(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.loaded = True
    self.tnx_cache = []
    self.w3 = Web3(Web3.WebsocketProvider(os.getenv("W3_NODE_WS")))
    with open("./data/rocketpool.json") as f:
      self.config = json.load(f)
    self.contracts = {}
    self.events = []
    storage = self.config['storage']
    with open(f"./contracts/{storage['name']}.abi", "r") as f:
      self.storage_contract = self.w3.eth.contract(address=storage["address"], abi=f.read())
    self.mapping = {}
    for name, events in self.config["sources"].items():
      address = self.get_address_from_storage_contract(name)
      with open(f"./contracts/{name}.abi", "r") as f:
        self.contracts[address] = self.w3.eth.contract(address=address, abi=f.read())
      for event in events:
        self.events.append(self.contracts[address].events[event].createFilter(fromBlock="latest", toBlock="latest"))
      self.mapping[address] = events
    if not self.run_loop.is_running():
      self.run_loop.start()

  def get_address_from_storage_contract(self, name):
    log.debug(f"retrieving address for {name}")
    sha3 = Web3.soliditySha3(["string", "string"], ["contract.address", name])
    return self.storage_contract.functions.getAddress(sha3).call()

  def get_abi_from_storage_contract(self, name):
    # Not used as the stored ABI is a stripped-down version without the required Events
    sha3 = Web3.soliditySha3(["string", "string"], ["contract.abi", name])
    raw_result = self.storage_contract.functions.getString(sha3).call()
    inflated = pako_inflate(base64.b64decode(raw_result))
    return inflated.decode("ascii")

  def get_proposal_info(self, event):
    contract = self.contracts[event['address']]
    result = {
      "message": contract.functions.getMessage(event["args"]["proposalID"]).call(),
      "votesFor": contract.functions.getVotesFor(event["args"]["proposalID"]).call() // 10**18,
      "votesAgainst": contract.functions.getVotesAgainst(event["args"]["proposalID"]).call() // 10**18,
    }
    return result

  def get_dao_member_name(self, member_address):
    address = self.get_address_from_storage_contract("rocketDAONodeTrusted")
    with open(f"./contracts/rocketDAONodeTrusted.abi", "r") as f:
      contract = self.w3.eth.contract(address=address, abi=f.read())
    return contract.functions.getMemberID(member_address).call()

  def create_embed(self, event_name, event):
    embed = Embed(color=discord.Color.from_rgb(235, 142, 85))
    embed.set_footer(text=os.getenv("CREDITS"), icon_url=os.getenv("CREDITS_ICON"))
    embed.set_author(icon_url="https://docs.rocketpool.net/images/logo.png", name="Rocket Pool Goerli")

    # prepare args
    args = dict(event['args'])
    for arg_key, arg_value in list(args.items()):
      if any(keyword in arg_key.lower() for keyword in ["amount", "value"]):
        args[arg_key] = arg_value / 10 ** 18

      if str(arg_value).startswith("0x"):
        args[f"{arg_key}_fancy"] = f"[{arg_value[:6]}...{arg_value[-4:]}](https://goerli.etherscan.io/search?q={arg_value})"

    # add proposal message manually if the event contains a proposal
    if "proposal" in event_name:
      data = self.get_proposal_info(event)
      args["message"] = data["message"]
      embed.add_field(name="Votes For", value=data["votesFor"], inline=False)
      embed.add_field(name="Votes Against", value=data["votesAgainst"], inline=False)

    # add member name if we can
    if "odao" in event_name:
      keys = [key for key in ["nodeAddress", "canceller", "executer", "proposer"] if key in args]
      if keys:
        key = keys[0]
        name = self.get_dao_member_name(args[key])
        if not name:
          name = "Unknown"
        args["member_fancy"] = f"{name} ({args[key + '_fancy']})"

    embed.title = _(f"rocketpool.{event_name}.title")
    embed.description = _(f"rocketpool.{event_name}.description", **args)

    tnx_hash = event['transactionHash'].hex()
    embed.add_field(name="Transaction Hash",
                    value=f"[{tnx_hash[:6]}...{tnx_hash[-4:]}](https://goerli.etherscan.io/tx/{tnx_hash})")
    if "from" in args:
      embed.add_field(name="Sender Address", value=args["from_fancy"])
    embed.add_field(name="Block Number",
                    value=f"[{event['blockNumber']}](https://goerli.etherscan.io/block/{event['blockNumber']})")
    return embed

  @tasks.loop(seconds=5.0)
  async def run_loop(self):
    if self.loaded:
      try:
        return await self.check_for_new_events()
      except Exception as err:
        self.loaded = False
        log.exception(err)
    else:
      try:
        return self.__init__(self.bot)
      except Exception as err:
        self.loaded = False
        log.exception(err)

  async def check_for_new_events(self):
    if not self.loaded:
      return
    log.debug("checking for new events")

    messages = []

    # Newest Event first so they are preferred over older ones.
    # Handles small reorgs better this way
    for events in reversed(self.events):
      for event in list(events.get_all_entries())[:1]:
        if event["event"] in self.mapping[event['address']]:

          # skip if we already have seen this message
          tnx_hash = event["transactionHash"]
          if tnx_hash in self.tnx_cache:
            continue

          event_name = self.mapping[event['address']][event["event"]]

          # lazy way of making it sort sensible within a single block
          score = event["blockNumber"] + (event["transactionIndex"] / 1000)

          messages.append({
            "score": score,
            "embed": self.create_embed(event_name, event)
          })

          # to prevent duplicate messages
          self.tnx_cache.append(tnx_hash)

          log.debug(event_name)
          print(event)

    channel = await self.bot.fetch_channel(os.getenv("OUTPUT_CHANNEL"))
    for embed in sorted(messages, key=lambda a: a["score"], reverse=False):
      await channel.send(embed=embed["embed"])

    # this is so we don't just continue and use up more and more memory for the deduplication
    self.tnx_cache = self.tnx_cache[-1000:]

  def cog_unload(self):
    self.loaded = False
    self.run_loop.cancel()


def setup(bot):
  bot.add_cog(RocketPool(bot))
