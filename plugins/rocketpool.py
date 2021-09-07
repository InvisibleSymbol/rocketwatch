import json
import logging
import os
import warnings

import discord
import termplotlib as tpl
from discord import Embed
from discord.ext import commands, tasks
from ens import ENS
from web3 import Web3
from web3.datastructures import MutableAttributeDict

from strings import _
from utils.shorten import short_hex

log = logging.getLogger("rocketpool")
log.setLevel(os.getenv("DEBUG_LEVEL"))


class RocketPool(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.loaded = True
    self.event_history = []
    self.contracts = {}
    self.events = []
    self.mapping = {}

    infura_id = os.getenv("INFURA_ID")
    self.w3 = Web3(Web3.WebsocketProvider(f"wss://goerli.infura.io/ws/v3/{infura_id}"))
    temp_mainnet_w3 = Web3(Web3.WebsocketProvider(f"wss://mainnet.infura.io/ws/v3/{infura_id}"))
    self.ens = ENS.fromWeb3(temp_mainnet_w3)  # switch to self.w3 once we use mainnet

    with open("./data/rocketpool.json") as f:
      self.config = json.load(f)

    # load storage contract so we can dynamically load all required addresses
    storage = self.config['storage']
    with open(f"./contracts/{storage['name']}.abi", "r") as f:
      self.storage_contract = self.w3.eth.contract(address=storage["address"], abi=f.read())

    # Load Contracts and create Filters for all Events
    for name, events in self.config["sources"].items():
      address = self.get_address_from_storage_contract(name)
      with open(f"./contracts/{name}.abi", "r") as f:
        self.contracts[address] = self.w3.eth.contract(address=address, abi=f.read())
      for event in events:
        self.events.append(
          self.contracts[address].events[event].createFilter(fromBlock="latest", toBlock="latest"))
      self.mapping[address] = events

    # This tracks all MinipoolStatus.Staking Events, which is indirectly used to track Minipool deposit events.
    # It is done this way to allow retrieving the public_key instantly from the corresponding transaction.
    # It usually takes some time till rocketMinipoolManager is updated with the key, so just accessing that wouldn't work.
    with open(f"./contracts/rocketMinipoolDelegate.abi", "r") as f:
      self.minipool_delegate_contract = self.w3.eth.contract(address=None, abi=f.read())
    event = self.minipool_delegate_contract.events.StatusUpdated.createFilter(fromBlock="latest",
                                                                              toBlock="latest",
                                                                              argument_filters={'status': 2})
    self.events.append(event)

    # load the Deposit Contract so we can parse Deposit Events
    with open(f"./contracts/beaconDepositContract.abi", "r") as f:
      self.deposit_contract = self.w3.eth.contract(address=os.getenv("DEPOSIT_CONTRACT_ADDRESS"), abi=f.read())

    if not self.run_loop.is_running():
      self.run_loop.start()

  def get_address_from_storage_contract(self, name):
    log.debug(f"retrieving address for {name}")
    sha3 = Web3.soliditySha3(["string", "string"], ["contract.address", name])
    return self.storage_contract.functions.getAddress(sha3).call()

  def get_proposal_info(self, event):
    contract = self.contracts[event['address']]
    result = {
      "message": contract.functions.getMessage(event.args.proposalID).call(),
      "votesFor": contract.functions.getVotesFor(event.args.proposalID).call() // 10 ** 18,
      "votesAgainst": contract.functions.getVotesAgainst(event.args.proposalID).call() // 10 ** 18,
    }
    return result

  def get_dao_member_name(self, member_address):
    address = self.get_address_from_storage_contract("rocketDAONodeTrusted")
    with open(f"./contracts/rocketDAONodeTrusted.abi", "r") as f:
      contract = self.w3.eth.contract(address=address, abi=f.read())
    return contract.functions.getMemberID(member_address).call()

  def handle_deposit(self, event):
    # attempt to retrieve the pubkey
    receipt = self.w3.eth.get_transaction_receipt(event.transactionHash)
    # next step will throw some warnings about other events but those are safe to ignore
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      processed_logs = self.deposit_contract.events.DepositEvent().processReceipt(receipt)

    if processed_logs:
      deposit_event = processed_logs[0]
      pubkey = "0x" + deposit_event.args.pubkey.hex()

      # first need to make the container mutable
      event = MutableAttributeDict(event)
      # so we can make this mutable
      event.args = MutableAttributeDict(event.args)
      event.args.pubkey = pubkey
      # while we are at it add the sender address so it shows up
      event.args["from"] = receipt["from"]
      # and add the minipool address, which is the contract that was called
      event.args["minipool"] = receipt["to"]

      return self.create_embed("minipool_deposit_event", event)

  def create_embed(self, event_name, event):
    embed = Embed(color=discord.Color.from_rgb(235, 142, 85))
    embed.set_footer(text=os.getenv("CREDITS"), icon_url=os.getenv("CREDITS_ICON"))

    # prepare args
    args = dict(event['args'])

    # add proposal message manually if the event contains a proposal
    if "proposal" in event_name:
      data = self.get_proposal_info(event)
      args["message"] = data["message"]
      # create bar graph for votes
      vote_graph = tpl.figure()
      vote_graph.barh([data["votesFor"], data["votesAgainst"]], ["For", "Against"], max_width=20)
      args["vote_graph"] = vote_graph.get_string()

    # create human readable decision for votes
    if "supported" in args:
      args["decision"] = "for" if args["supported"] else "against"

    # show public key if we have one
    if "pubkey" in args:
      embed.add_field(name="Validator",
                      value=f"[{short_hex(args['pubkey'])}](https://prater.beaconcha.in/validator/{args['pubkey']})",
                      inline=False)

    for arg_key, arg_value in list(args.items()):
      if any(keyword in arg_key.lower() for keyword in ["amount", "value"]):
        args[arg_key] = arg_value / 10 ** 18

      if str(arg_value).startswith("0x"):
        name = ""
        if self.w3.isAddress(arg_value):
          name = self.ens.name(arg_value)
        if not name:
          # fallback when no ens name is found or when the hex isn't an address to begin with
          name = f"{short_hex(arg_value)}"
        args[f"{arg_key}_fancy"] = f"[{name}](https://goerli.etherscan.io/search?q={arg_value})"

    # add oDAO member name if we can
    if "odao" in event_name:
      keys = [key for key in ["nodeAddress", "canceller", "executer", "proposer", "voter"] if key in args]
      if keys:
        key = keys[0]
        name = self.get_dao_member_name(args[key])
        if name:
          args["member_fancy"] = f"[{name}](https://goerli.etherscan.io/search?q={args[key]})"
        else:
          # fallback to just using the pre-formatted address instead
          args["member_fancy"] = args[key + '_fancy']

    embed.title = _(f"rocketpool.{event_name}.title")
    embed.description = _(f"rocketpool.{event_name}.description", **args)

    tnx_hash = event['transactionHash'].hex()
    embed.add_field(name="Transaction Hash",
                    value=f"[{short_hex(tnx_hash)}](https://goerli.etherscan.io/tx/{tnx_hash})")

    if "from" in args:
      embed.add_field(name="Sender Address",
                      value=args["from_fancy"])

    embed.add_field(name="Block Number",
                    value=f"[{event['blockNumber']}](https://goerli.etherscan.io/block/{event['blockNumber']})")
    return embed

  @tasks.loop(seconds=15.0)
  async def run_loop(self):
    if self.loaded:
      try:
        return await self.check_for_new_events()
      except Exception as err:
        self.loaded = False
        log.exception(err)
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
    for events in self.events:
      log.debug(f"checking {events.filter_id} ({events.filter_params})")
      for event in reversed(list(events.get_new_entries())):
        log.debug(f"checking event {event}")
        tnx_hash = event.transactionHash
        address = event.address

        # skip if we already have seen this message
        event_hash = [tnx_hash, event.event, event.args]
        if event_hash in self.event_history:
          # TODO don't just use the tnx_hash alone so we can support multiple events in a single message (add topics or smth idk)
          log.debug(f"skipping {event_hash} because we have already processed it")
          continue
        self.event_history = self.event_history[-256:] + [event_hash]

        # lazy way of making it sort events within a single block correctly
        score = event.blockNumber + (event.transactionIndex / 1000)
        embed = None
        event_name = None

        # custom Deposit Event Path
        if event.event == "StatusUpdated":
          embed = self.handle_deposit(event)
          event_name = "minipool_deposit"

        # default Event Path
        elif event.event in self.mapping.get(address, {}):
          event_name = self.mapping[address][event.event]

          embed = self.create_embed(event_name, event)

        if embed:
          messages.append({
            "score": score,
            "embed": embed,
            "event_name": event_name
          })

    log.debug("finished checking for new events")

    default_channel = await self.bot.fetch_channel(os.getenv("DEFAULT_CHANNEL"))
    odao_channel = await self.bot.fetch_channel(os.getenv("ODAO_CHANNEL"))
    for message in sorted(messages, key=lambda a: a["score"], reverse=False):
      if "odao" in message["event_name"]:
        await odao_channel.send(embed=message["embed"])
      else:
        await default_channel.send(embed=message["embed"])

  def cog_unload(self):
    self.loaded = False
    self.run_loop.cancel()


def setup(bot):
  bot.add_cog(RocketPool(bot))
