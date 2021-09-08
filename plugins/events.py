import json
import logging
import os

import termplotlib as tpl
from discord import Embed, Color
from discord.ext import commands, tasks
from web3 import Web3
from web3.datastructures import MutableAttributeDict

from strings import _
from utils.cached_ens import CachedEns
from utils.rocketpool import RocketPool
from utils.shorten import short_hex

log = logging.getLogger("rocketpool")
log.setLevel(os.getenv("LOG_LEVEL"))

DEPOSIT_EVENT = 2
EXIT_EVENT = 4


class Events(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.loaded = True
    self.event_history = []
    self.storage_cache = {}
    self.contracts = {}
    self.address_to_contract = {}
    self.events = []
    self.mapping = {}

    infura_id = os.getenv("INFURA_ID")
    self.w3 = Web3(Web3.WebsocketProvider(f"wss://goerli.infura.io/ws/v3/{infura_id}"))
    temp_mainnet_w3 = Web3(Web3.WebsocketProvider(f"wss://mainnet.infura.io/ws/v3/{infura_id}"))
    self.ens = CachedEns(temp_mainnet_w3)  # switch to self.w3 once we use mainnet
    self.rocketpool = RocketPool(self.w3,
                                 os.getenv("STORAGE_CONTRACT"),
                                 os.getenv("DEPOSIT_CONTRACT"))

    with open("./config/events.json") as f:
      mapped_events = json.load(f)

    # Load Contracts and create Filters for all Events
    for contract_name, event_mapping in mapped_events.items():
      contract = self.rocketpool.get_contract_by_name(contract_name)
      for event in event_mapping:
        self.events.append(contract.events[event].createFilter(fromBlock="latest", toBlock="latest"))
      self.mapping[contract_name] = event_mapping

    # Track MinipoolStatus.Staking and MinipoolStatus.Withdrawable Events.
    minipool_delegate_contract = self.rocketpool.get_contract(name="rocketMinipoolDelegate")
    self.events.append(minipool_delegate_contract.events.StatusUpdated.createFilter(fromBlock="latest",
                                                                                    toBlock="latest",
                                                                                    argument_filters={
                                                                                      'status': [DEPOSIT_EVENT,
                                                                                                 EXIT_EVENT]}))

    if not self.run_loop.is_running():
      self.run_loop.start()

  def handle_minipool_events(self, event):
    receipt = self.w3.eth.get_transaction_receipt(event.transactionHash)

    if not self.rocketpool.is_minipool(receipt["to"]):
      # some random contract we don't care about
      log.warning(f"Skipping {event.transactionHash} because the called Contract is not a Minipool")
      return

    # first need to make the container mutable
    event = MutableAttributeDict(event)
    # so we can make this mutable
    event.args = MutableAttributeDict(event.args)

    pubkey = self.rocketpool.get_pubkey_using_transaction(receipt)
    if not pubkey:
      # check if the contract has it stored instead
      pubkey = self.rocketpool.get_pubkey_using_contract(receipt["from"])

    if pubkey:
      event.args.pubkey = pubkey

    # while we are at it add the sender address so it shows up
    event.args["from"] = receipt["from"]
    # and add the minipool address, which is the contract that was called
    event.args["minipool"] = receipt["to"]

    event_name = "minipool_deposit_event" if event.args.status == DEPOSIT_EVENT else "minipool_exited_event"
    return self.create_embed(event_name, event), event_name

  def create_embed(self, event_name, event):
    embed = Embed(color=Color.from_rgb(235, 142, 85))
    embed.set_footer(text=os.getenv("CREDITS"), icon_url=os.getenv("CREDITS_ICON"))

    # prepare args
    args = dict(event['args'])

    # add proposal message manually if the event contains a proposal
    if "proposal" in event_name:
      data = self.rocketpool.get_proposal_info(event)
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
        args[arg_key] = round(arg_value / 10 ** 18, 5)
        if args[arg_key] == int(args[arg_key]):
          args[arg_key] = int(args[arg_key])

      if str(arg_value).startswith("0x"):
        name = ""
        if self.w3.isAddress(arg_value):
          name = self.ens.get_name(arg_value)
        if not name:
          # fallback when no ens name is found or when the hex isn't an address to begin with
          name = f"{short_hex(arg_value)}"
        args[f"{arg_key}_fancy"] = f"[{name}](https://goerli.etherscan.io/search?q={arg_value})"

    # add oDAO member name if we can
    if "odao" in event_name:
      keys = [key for key in ["nodeAddress", "canceller", "executer", "proposer", "voter"] if key in args]
      if keys:
        key = keys[0]
        name = self.rocketpool.get_dao_member_name(args[key])
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
      log.debug(f"checking topic: {events.filter_params['topics'][0]} address: {events.filter_params.get('address', None)}")
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
          embed, event_name = self.handle_minipool_events(event)

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

    if messages:
      log.info(f"Sending {len(messages)} Message(s)")
      default_channel = await self.bot.fetch_channel(os.getenv("DEFAULT_CHANNEL"))
      odao_channel = await self.bot.fetch_channel(os.getenv("ODAO_CHANNEL"))
      for message in sorted(messages, key=lambda a: a["score"], reverse=False):
        log.info(f"Sending \"{message['event_name']}\" Event")
        if "odao" in message["event_name"]:
          await odao_channel.send(embed=message["embed"])
        else:
          await default_channel.send(embed=message["embed"])
      log.info("Finished sending Message(s)")

  def cog_unload(self):
    self.loaded = False
    self.run_loop.cancel()


def setup(bot):
  bot.add_cog(Events(bot))
