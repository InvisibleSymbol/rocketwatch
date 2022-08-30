import json
import logging
import random

import pymongo
import termplotlib as tpl
from discord.ext import commands
from web3.datastructures import MutableAttributeDict as aDict
from web3.exceptions import ABIEventFunctionNotFound

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.solidity import SUBMISSION_KEYS

log = logging.getLogger("events")
log.setLevel(cfg["log_level"])

DEPOSIT_EVENT = 2
WITHDRAWABLE_EVENT = 3


class QueuedEvents(commands.Cog):
    update_block = 0

    def __init__(self, bot):
        rp.flush()
        self.bot = bot
        self.state = "INIT"
        self.events = []
        self.internal_event_mapping = {}
        self.topic_mapping = {}
        self.mongo = pymongo.MongoClient(cfg["mongodb_uri"])
        self.db = self.mongo.rocketwatch

        with open("./plugins/events/events.json") as f:
            events_config = json.load(f)

        try:
            latest_block = self.db.last_checked_block.find_one({"_id": "events"})["block"]
            self.start_block = latest_block - cfg["core.look_back_distance"]
        except Exception as err:
            log.error(f"Failed to get latest block from db: {err}")
            self.start_block = w3.eth.getBlock("latest").number - cfg["core.look_back_distance"]

        # Generate Filter for direct Events
        addresses = []
        aggregated_topics = []
        for group in events_config["direct"]:
            try:
                contract = rp.get_contract_by_name(group["contract_name"])
            except Exception as err:
                log.error(f"Failed to get contract {group['contract_name']}: {err}")
                continue
            addresses.append(contract.address)

            for event in group["events"]:
                try:
                    topic = contract.events[event["event_name"]].build_filter().topics[0]
                except ABIEventFunctionNotFound as err:
                    log.exception(err)
                    log.warning(
                        f"Skipping {event['event_name']} ({event['name']}) as it can't be found in the contract")
                    continue

                self.internal_event_mapping[event["event_name"]] = event["name"]
                self.topic_mapping[topic] = event["event_name"]
                if topic not in aggregated_topics:
                    aggregated_topics.append(topic)

        if addresses:
            self.events.append(w3.eth.filter({
                "address"  : addresses,
                "topics"   : [aggregated_topics],
                "fromBlock": self.start_block,
                "toBlock"  : "latest"
            }))

        # Generate Filters for global Events
        for group in events_config["global"]:
            contract = rp.assemble_contract(name=group["contract_name"])
            for event in group["events"]:
                try:
                    f = event.get("filter", {})
                    self.events.append(contract.events[event["event_name"]].createFilter(fromBlock=self.start_block,
                                                                                         toBlock="latest",
                                                                                         argument_filters=f))
                except ABIEventFunctionNotFound as err:
                    log.exception(err)
                    log.warning(
                        f"Skipping {event['event_name']} ({event['name']}) as it can't be found in the contract")
                    continue
                self.internal_event_mapping[event["event_name"]] = event["name"]

    def handle_global_event(self, event):
        receipt = w3.eth.get_transaction_receipt(event.transactionHash)
        event_name = self.internal_event_mapping[event["event"]]

        if not any([rp.call("rocketMinipoolManager.getMinipoolExists", receipt.to),
                    rp.get_address_by_name("rocketNodeDeposit") == receipt.to]):
            # some random contract we don't care about
            log.warning(f"Skipping {event.transactionHash.hex()} because the called Contract is not a Minipool")
            return None

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
        event.args.tnx_fee_dai = rp.get_dai_eth_price() * event.args.tnx_fee

        return self.create_embed(event_name, event), event_name

    def create_embed(self, event_name, event):
        # prepare args
        args = aDict(event['args'])
        if "submission" in args:
            args.submission = aDict(dict(zip(SUBMISSION_KEYS, args.submission)))

        if "otc_swap" in event_name:
            # signer = seller
            # sender = buyer
            # either the selling or buying token has to be the RPL token
            rpl = rp.get_address_by_name("rocketTokenRPL")
            if args.signerToken != rpl and args.senderToken != rpl:
                return None
            args.seller = w3.toChecksumAddress(f"0x{event.topics[2][-40:]}")
            args.buyer = w3.toChecksumAddress(f"0x{event.topics[3][-40:]}")
            # token amounts
            args.sellAmount = args.signerAmount
            args.buyAmount = args.senderAmount
            # token names
            args.sellToken = rp.assemble_contract(name="ERC20", address=args.signerToken).functions.symbol().call()
            args.buyToken = rp.assemble_contract(name="ERC20", address=args.senderToken).functions.symbol().call()
            # RPL/- exchange rate
            if args.signerToken == rpl:
                args.exchangeRate = args.buyAmount / args.sellAmount
                args.otherToken = args.buyToken
            else:
                args.exchangeRate = args.sellAmount / args.buyAmount
                args.otherToken = args.sellToken
            if args.otherToken.lower() == "wETH":
                # get exchange rate from rp
                args.marketExchangeRate = rp.call("rocketNetworkPrices.getRPLPrice")
                # calculate the discount received compared to the market price
                args.discountAmount = (1 - args.exchangeRate / solidity.to_float(args.marketExchangeRate)) * 100

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

        # create human-readable decision for votes
        if "supported" in args:
            args.decision = "for" if args.supported else "against"

        # add inflation and new supply if inflation occurred
        if "rpl_inflation" in event_name:
            args.total_supply = int(solidity.to_float(rp.call("rocketTokenRPL.totalSupply")))
            args.inflation = round(rp.get_annual_rpl_inflation() * 100, 4)

        if "auction_bid_event" in event_name:
            eth = solidity.to_float(args.bidAmount)
            price = solidity.to_float(
                rp.call("rocketAuctionManager.getLotPriceAtBlock", args.lotIndex, args.blockNumber))
            args.rplAmount = eth / price

        if event_name in ["rpl_claim_event", "rpl_stake_event", "rpl_withdraw_event"]:
            # get eth price by multiplying the amount by the current RPL ratio
            rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
            args.amount = solidity.to_float(args.amount)
            args.ethAmount = args.amount * rpl_ratio
        if event_name in ["node_merkle_rewards_claimed"]:
            rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
            args.amountRPL = sum(solidity.to_float(r) for r in args.amountRPL)
            args.amountETH = sum(solidity.to_float(e) for e in args.amountETH)
            args.ethAmount = args.amountRPL * rpl_ratio

        # reject if the amount is not major
        if any(["rpl_claim_event" in event_name and args.ethAmount < 5,
                "rpl_stake_event" in event_name and args.amount < 1000,
                "node_merkle_rewards_claimed" in event_name and args.ethAmount < 5 and args.amountETH < 5,
                "rpl_withdraw_event" in event_name and args.ethAmount < 16]):
            log.debug(f"Skipping {event_name} because the event ({args.ethAmount}) is too small to be interesting")
            return None

        if "claimingContract" in args and args.claimingAddress == args.claimingContract:
            possible_contracts = [
                "rocketClaimNode",
                "rocketClaimTrustedNode",
                "rocketClaimDAO",
            ]

            # loop over all possible contracts if we get a match return empty response
            for contract in possible_contracts:
                if rp.get_address_by_name(contract) == args.claimingContract:
                    return None

        if "minipool_prestake_event" in event_name:
            contract = rp.assemble_contract("rocketMinipoolDelegate", args.minipool)
            args.commission = solidity.to_float(contract.functions.getNodeFee().call())

        if "node_register_event" in event_name:
            args.timezone = rp.call("rocketNodeManager.getNodeTimezoneLocation", args.node)
        if "odao_member_challenge_event" in event_name:
            args.challengeDeadline = args.time + rp.call("rocketDAONodeTrustedSettingsMembers.getChallengeWindow")
        if "odao_member_challenge_decision_event" in event_name:
            if args.success:
                args.event_name = "odao_member_challenge_accepted_event"
                # get their RPL bond that was burned by querying the previous block
                args.rplBondAmount = solidity.to_float(rp.call("rocketDAONodeTrusted.getMemberRPLBondAmount",
                                                               args.nodeChallengedAddress,
                                                               block=args.blockNumber - 1))
                args.sender = args.nodeChallengeDeciderAddress
            else:
                args.event_name = "odao_member_challenge_rejected_event"
        if "node_smoothing_pool_state_changed" in event_name:
            # geet minipool count
            args.minipoolCount = rp.call("rocketMinipoolManager.getNodeMinipoolCount", args.node)
            if args.state:
                args.event_name = "node_smoothing_pool_joined"
            else:
                args.event_name = "node_smoothing_pool_left"
        if "node_merkle_rewards_claimed" in event_name:
            if args.amountETH > 0:
                args.event_name = "node_merkle_rewards_claimed_both"
            else:
                args.event_name = "node_merkle_rewards_claimed_rpl"
        args = prepare_args(args)
        return assemble(args)

    def run_loop(self):
        if self.state == "RUNNING":
            log.error("Boostrap plugin was interrupted while running. Re-initializing...")
            self.__init__(self.bot)
        return self.check_for_new_events()

    def check_for_new_events(self):
        log.info("Checking for new Events")

        messages = []
        do_full_check = self.state == "INIT"
        if do_full_check:
            log.info("Doing full check")
        self.state = "RUNNING"
        should_reinit = False

        for events in self.events:
            if do_full_check:
                pending_events = events.get_all_entries()
            else:
                pending_events = events.get_new_entries()
            for event in reversed(list(pending_events)):
                tnx_hash = event.transactionHash.hex()
                embed = None
                event_name = None

                if event.get("removed", False):
                    continue

                log.debug(f"Checking Event {event}")

                address = event.address
                if n:=rp.get_name_by_address(address) and "topics" in event:
                    log.info(f"Found event {event} for {n}")
                    # default event path
                    contract = rp.get_contract_by_address(address)
                    contract_event = self.topic_mapping[event.topics[0].hex()]
                    topics = [w3.toHex(t) for t in event.topics]
                    event = aDict(contract.events[contract_event]().processLog(event))
                    event.topics = topics
                    event_name = self.internal_event_mapping[event.event]

                    embed = self.create_embed(event_name, event)
                elif event.get("event", None) in self.internal_event_mapping:
                    if self.internal_event_mapping[event.event] in ["contract_upgraded", "contract_added"]:
                        if event.blockNumber > self.update_block:
                            log.info("detected update, setting reinit flag")
                            should_reinit = True
                            self.update_block = event.blockNumber
                    else:
                        # deposit/exit event path
                        embed, event_name = self.handle_global_event(event)

                if embed:
                    # lazy way of making it sort events within a single block correctly
                    score = event.blockNumber
                    # sort within block
                    score += event.transactionIndex * 10 ** -3
                    # sort within transaction
                    if "logIndex" in event:
                        score += event.logIndex * 10 ** -3

                    messages.append(Response(
                        embed=embed,
                        topic="events",
                        event_name=event_name,
                        unique_id=f"{tnx_hash}:{event_name}",
                        block_number=event.blockNumber,
                        transaction_index=event.transactionIndex,
                        event_index=event.logIndex
                    ))
                if event.blockNumber > self.start_block:
                    self.start_block = event.blockNumber

        log.debug("Finished Checking for new Events")
        # store last checked block in db if its bigger than the one we have stored
        self.state = "OK"
        self.db.last_checked_block.replace_one({"_id": "events"}, {"_id": "events", "block": self.start_block},
                                               upsert=True)
        if should_reinit:
            log.info("detected update, triggering reinit")
            self.state = "RUNNING"
        return messages


async def setup(bot):
    await bot.add_cog(QueuedEvents(bot))
