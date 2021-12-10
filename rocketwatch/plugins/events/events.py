import asyncio
import json
import logging

import termplotlib as tpl
from cachetools import FIFOCache
from discord.ext import commands, tasks
from web3.datastructures import MutableAttributeDict as aDict
from web3.exceptions import ABIEventFunctionNotFound

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args, exception_fallback
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.shared_w3 import w3

log = logging.getLogger("events")
log.setLevel(cfg["log_level"])

DEPOSIT_EVENT = 2
WITHDRAWABLE_EVENT = 3


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = "OK"
        self.tnx_hash_cache = FIFOCache(maxsize=256)
        self.events = []
        self.internal_event_mapping = {}
        self.topic_mapping = {}

        with open("./plugins/events/events.json") as f:
            events_config = json.load(f)

        # Generate Filter for direct Events
        addresses = []
        aggregated_topics = []
        for group in events_config["direct"]:
            contract = rp.get_contract_by_name(group["contract_name"])
            addresses.append(contract.address)

            for event in group["events"]:
                try:
                    topic = contract.events[event["event_name"]].build_filter().topics[0]
                except ABIEventFunctionNotFound as err:
                    log.exception(err)
                    log.warning(f"Skipping {event['event_name']} ({event['name']}) as it can't be found in the contract")
                    continue

                self.internal_event_mapping[event["event_name"]] = event["name"]
                self.topic_mapping[topic] = event["event_name"]
                if topic not in aggregated_topics:
                    aggregated_topics.append(topic)

        self.events.append(w3.eth.filter({
            "address"  : addresses,
            "topics"   : [aggregated_topics],
            "fromBlock": "latest",
            "toBlock"  : "latest"
        }))

        # Generate Filters for global Events
        for group in events_config["global"]:
            contract = rp.assemble_contract(name=group["contract_name"])
            for event in group["events"]:
                try:
                    f = event.get("filter", {})
                    self.events.append(contract.events[event["event_name"]].createFilter(fromBlock="latest",
                                                                                         toBlock="latest",
                                                                                         argument_filters=f))
                except ABIEventFunctionNotFound as err:
                    log.exception(err)
                    log.warning(f"Skipping {event['event_name']} ({event['name']}) as it can't be found in the contract")
                    continue
                self.internal_event_mapping[event["event_name"]] = event["name"]

        if not self.run_loop.is_running():
            self.run_loop.start()

    @exception_fallback()
    async def handle_global_event(self, event):
        receipt = w3.eth.get_transaction_receipt(event.transactionHash)
        event_name = self.internal_event_mapping[event["event"]]

        if not any([rp.call("rocketMinipoolManager.getMinipoolExists", receipt.to),
                    rp.get_address_by_name("rocketNodeDeposit") == receipt.to]):
            # some random contract we don't care about
            log.warning(f"Skipping {event.transactionHash.hex()} because the called Contract is not a Minipool")
            return Response()

        # first need to make the container mutable
        event = aDict(event)
        # so we can make the args mutable
        event.args = aDict(event.args)

        pubkey = None

        # is the pubkey in the event arguments?
        if "validatorPubkey" in event.args:
            pubkey = event.args.validatorPubkey.hex()

        # maybe the contract has it stored?
        if not pubkey:
            pubkey = rp.call("rocketMinipoolManager.getMinipoolPubkey", event.address).hex()

        # maybe its in the transaction?
        if not pubkey:
            pubkey = rp.get_pubkey_using_transaction(receipt)

        if pubkey:
            event.args.pubkey = "0x" + pubkey

        # while we are at it add the sender address, so it shows up
        event.args["from"] = receipt["from"]

        # and add the minipool address, which is the origin of the event
        event.args.minipool = event.address

        # and add the transaction fee
        event.args.tnx_fee = solidity.to_float(receipt["gasUsed"] * receipt["effectiveGasPrice"])
        event.args.tnx_fee_dai = rp.get_dai_eth_price() * event.tnx_fee

        return await self.create_embed(event_name, event)

    @exception_fallback()
    async def create_embed(self, event_name, event):
        # prepare args
        args = aDict(event['args'])

        if "tnx_fee" not in args:
            receipt = w3.eth.get_transaction_receipt(event.transactionHash)
            args.tnx_fee = solidity.to_float(receipt["gasUsed"] * receipt["effectiveGasPrice"])
            args.tnx_fee_dai = rp.get_dai_eth_price() * args.tnx_fee

        # store event_name in args
        args.event_name = event_name

        # add transaction hash and block number to args
        args.transactionHash = event.transactionHash.hex()
        args.blockNumber = event.blockNumber

        # add proposal message manually if the event contains a proposal
        if "proposal" in event_name:
            proposal_id = event.args.proposalID
            args.message = rp.call("rocketDAOProposal.getMessage", proposal_id)
            # create bar graph for votes
            votes = [
                solidity.to_int(rp.call("rocketDAOProposal.getVotesFor", proposal_id)),
                solidity.to_int(rp.call("rocketDAOProposal.getVotesAgainst", proposal_id))
            ]
            vote_graph = tpl.figure()
            vote_graph.barh(votes, ["For", "Against"], max_width=20)
            args.vote_graph = vote_graph.get_string()

        # create human readable decision for votes
        if "supported" in args:
            args.decision = "for" if args.supported else "against"

        # add inflation and new supply if inflation occurred
        if "rpl_inflation" in event_name:
            args.total_supply = int(solidity.to_float(rp.call("rocketTokenRPL.totalSupply")))
            args.inflation = round(rp.get_annual_rpl_inflation() * 100, 4)

        if "auction_bid_event" in event_name:
            eth = solidity.to_float(args.bidAmount)
            price = solidity.to_float(rp.call("rocketAuctionManager.getLotPriceAtBlock", args.lotIndex, args.blockNumber))
            args.rplAmount = eth / price

        if event_name in ["rpl_claim_event", "rpl_stake_event"]:
            # get eth price by multiplying the amount by the current RPL ratio
            rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
            args.ethAmount = solidity.to_float(args.amount) * rpl_ratio

        # reject if the amount is not major
        if any(["rpl_claim_event" in event_name and args.ethAmount < 10,
                "rpl_stake_event" in event_name and args.ethAmount < 160]):
            log.debug(f"Skipping {event_name} because the amount ({args.ethAmount}) is too small to be interesting")
            return Response()

        if "claimingContract" in args and args.claimingAddress == args.claimingContract:
            possible_contracts = [
                "rocketClaimNode",
                "rocketClaimTrustedNode",
                "rocketClaimDAO",
            ]

            # loop over all possible contracts if we get a match return empty response
            for contract in possible_contracts:
                if rp.get_address_by_name(contract) == args.claimingContract:
                    return Response()

        if "minipool_prestake_event" in event_name:
            contract = rp.assemble_contract("rocketMinipoolDelegate", args.minipool)
            args.commission = solidity.to_float(contract.functions.getNodeFee().call())

        args = prepare_args(args)
        return Response(
            embed=assemble(args),
            event_name=event_name)

    @tasks.loop(seconds=15.0)
    async def run_loop(self):
        if self.state == "STOPPED":
            return

        if self.state != "ERROR":
            try:
                self.state = "OK"
                return await self.check_for_new_events()
            except Exception as err:
                self.state = "ERROR"
                await report_error(err)
        try:
            return self.__init__(self.bot)
        except Exception as err:
            self.state = "ERROR"
            await report_error(err)

    async def check_for_new_events(self):
        log.info("Checking for new Events")

        messages = []
        tnx_hashes = []

        for events in self.events:
            for event in reversed(list(events.get_new_entries())):
                # small delay to make commands not timeout
                await asyncio.sleep(0.01)
                tnx_hash = event.transactionHash.hex()
                result = None

                if event.get("removed", False) or tnx_hash in self.tnx_hash_cache:
                    continue

                log.debug(f"Checking Event {event}")

                address = event.address
                contract_name = rp.get_name_by_address(address)
                if contract_name:
                    # default event path
                    contract = rp.get_contract_by_address(address)
                    contract_event = self.topic_mapping[event.topics[0].hex()]
                    event = contract.events[contract_event]().processLog(event)
                    event_name = self.internal_event_mapping[event.event]

                    result = await self.create_embed(event_name, event)
                elif event.get("event", None) in self.internal_event_mapping:
                    if tnx_hash in tnx_hashes:
                        log.debug("Skipping Event as we have already seen it. (Double statusUpdated Emit Bug)")
                        continue
                    # deposit/exit event path
                    result = await self.handle_global_event(event)

                if result:
                    # lazy way of making it sort events within a single block correctly
                    score = event.blockNumber
                    # sort within block
                    score += event.transactionIndex * 10 ** -3
                    # sort within transaction
                    if "logIndex" in event:
                        score += event.logIndex * 10 ** -3

                    messages.append(aDict({
                        "score" : score,
                        "result": result
                    }))

                tnx_hashes.append(tnx_hash)

        log.debug("Finished Checking for new Events")

        if messages:
            log.info(f"Sending {len(messages)} Message(s)")

            channels = cfg["discord.channels"]

            for message in sorted(messages, key=lambda a: a["score"], reverse=False):
                log.debug(f"Sending \"{message.result.event_name}\" Event")
                channel_candidates = [value for key, value in channels.items() if message.result.event_name.startswith(key)]
                channel = await self.bot.fetch_channel(channel_candidates[0] if channel_candidates else channels['default'])
                await channel.send(embed=message.result.embed)

            log.info("Finished sending Message(s)")

        # de-dupe logic:
        for tnx_hash in set(tnx_hashes):
            self.tnx_hash_cache[tnx_hash] = True

    def cog_unload(self):
        self.state = "STOPPED"
        self.run_loop.cancel()


def setup(bot):
    bot.add_cog(Events(bot))
