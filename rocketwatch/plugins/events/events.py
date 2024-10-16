import json
import logging
import warnings

import pymongo
from typing import Union
from discord import Object
from discord.app_commands import guilds
from discord.ext.commands import Cog, Context, is_owner, hybrid_command
from web3.datastructures import MutableAttributeDict as aDict, AttributeDict as immutableADict
from web3.exceptions import ABIEventFunctionNotFound
from web3.types import LogReceipt, EventData

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args, el_explorer_url
from utils.rocketpool import rp, NoAddressFound
from utils.shared_w3 import w3, bacon
from utils.solidity import SUBMISSION_KEYS
from utils.dao import DefaultDAO, ProtocolDAO

log = logging.getLogger("events")
log.setLevel(cfg["log_level"])


class QueuedEvents(Cog):
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
            contract_name = group["contract_name"]
            try:
                contract = rp.get_contract_by_name(contract_name)
            except NoAddressFound as err:
                log.error(f"Failed to get contract {contract_name}: {err}")
                continue
            addresses.append(contract.address)

            for event in group["events"]:
                event_name = event["event_name"]
                try:
                    topic = contract.events[event_name].build_filter().topics[0]
                except ABIEventFunctionNotFound as err:
                    log.exception(err)
                    log.warning(
                        f"Skipping {event_name} ({event['name']}) as it can't be found in the contract")
                    continue

                self.internal_event_mapping[f"{contract_name}.{event_name}"] = event["name"]
                self.topic_mapping[topic] = event_name
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
                    rp.call("rocketMinipoolManager.getMinipoolExists", event.address),
                    rp.get_name_by_address(receipt.to),
                    rp.get_name_by_address(event.address)]):
            # some random contract we don't care about
            log.warning(f"Skipping {event.transactionHash.hex()} because the called Contract is not a Minipool")
            return None, None

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
        if rp.get_name_by_address(receipt["to"]) is None:
            event.args["from"] = receipt["to"]
            event.args["caller"] = receipt["from"]

        # and add the minipool address, which is the origin of the event
        event.args.minipool = event.address

        # and add the transaction fee
        event.args.tnx_fee = solidity.to_float(receipt["gasUsed"] * receipt["effectiveGasPrice"])
        event.args.tnx_fee_dai = rp.get_dai_eth_price() * event.args.tnx_fee

        return self.create_embed(event_name, event), event_name

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def trigger_event(
            self,
            ctx: Context,
            contract: str,
            event: str,
            json_args: str = "{}",
            block_number: int = 0
    ):
        await ctx.defer()
        try:
            default_args = {
                "tnx_fee": 0,
                "tnx_fee_dai": 0
            }
            event_obj = aDict({
                "event": event,
                "transactionHash": aDict({"hex": lambda: '0x0000000000000000000000000000000000000000'}),
                "blockNumber": block_number,
                "args": aDict(default_args | json.loads(json_args))
            })
        except json.JSONDecodeError:
            return await ctx.send(content="Invalid JSON args!")

        if not (event_name := self.internal_event_mapping.get(event, None)):
            event_name = self.internal_event_mapping[f"{contract}.{event}"]

        if embed := self.create_embed(event_name, event_obj):
            await ctx.send(embed=embed)
        else:
            await ctx.send(content="No events triggered.")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def replay_events(self, ctx: Context, tx_hash: str):
        await ctx.defer()
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        logs: list[LogReceipt] = receipt.logs

        filtered_events: list[Union[LogReceipt, EventData]] = []

        # get direct events
        for event_log in logs:
            if ("topics" in event_log) and (event_log["topics"][0].hex() in self.topic_mapping):
                filtered_events.append(event_log)

        # get global events
        with open("./plugins/events/events.json") as f:
            global_events = json.load(f)["global"]

        for group in global_events:
            contract = rp.assemble_contract(name=group["contract_name"])
            for event in group["events"]:
                try:
                    event = contract.events[event["event_name"]]()
                    rich_logs = event.process_receipt(receipt)
                    filtered_events.extend(rich_logs)
                except ABIEventFunctionNotFound:
                    continue

        _, responses = self.process_events(filtered_events)
        if not responses:
            await ctx.send(content="No events found.")

        for response in responses:
            await ctx.send(embed=response.embed)

    def create_embed(self, event_name, event):
        args = aDict(event['args'])

        if "negative_rETH_ratio_update_event" in event_name:
            args.currRETHRate = solidity.to_float(args.totalEth) / solidity.to_float(args.rethSupply)
            args.prevRETHRate = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate", block=event.blockNumber - 1))
            d = args.currRETHRate - args.prevRETHRate
            if d > 0 or abs(d) < 0.00001:
                return None

        if "price_update_event" in event_name:
            args.value = args.rplPrice
            next_period = rp.call("rocketRewardsPool.getClaimIntervalTimeStart", block=event.blockNumber) + rp.call("rocketRewardsPool.getClaimIntervalTime", block=event.blockNumber)
            args.rewardPeriodEnd = next_period
            update_rate = rp.call("rocketDAOProtocolSettingsNetwork.getSubmitPricesFrequency", block=event.blockNumber) # in seconds
            # get timestamp of event block
            ts = w3.eth.getBlock(event.blockNumber).timestamp
            # check if the next update is after the next period ts
            earliest_next_update = ts + update_rate
            # if it will update before the next period, skip
            if earliest_next_update < next_period:
                return None

        if event_name == "bootstrap_pdao_setting_multi_event":
            description_parts = []
            for i in range(len(args.settingContractNames)):
                value_raw = args.values[i]
                match args.types[i]:
                    case 0:
                        # SettingType.UINT256
                        value = w3.toInt(value_raw)
                    case 1:
                        # SettingType.BOOL
                        value = bool(value_raw)
                    case 2:
                        # SettingType.ADDRESS
                        value = w3.toChecksumAddress(value_raw)
                    case _:
                        value = "???"
                description_parts.append(
                    f"`{args.settingPaths[i]}` set to `{value}`"
                )
            args.description = "\n".join(description_parts)
        elif event_name == "bootstrap_pdao_claimer_event":
            def share_repr(percentage: float) -> str:
                max_width = 35
                num_points = round(max_width * percentage / 100)
                return '*' * num_points

            node_share = args.nodePercent / 10 ** 16
            pdao_share = args.protocolPercent / 10 ** 16
            odao_share = args.trustedNodePercent / 10 ** 16

            args.description = '\n'.join([
                f"Node Operator Share",
                f"{share_repr(node_share)} {node_share:.1f}%",
                f"Protocol DAO Share",
                f"{share_repr(pdao_share)} {pdao_share:.1f}%",
                f"Oracle DAO Share",
                f"{share_repr(odao_share)} {odao_share:.1f}%",
            ])
        elif event_name == "bootstrap_sdao_member_kick_event":
            args.memberAddress = el_explorer_url(args.memberAddress, block=(event.blockNumber - 1))
        elif event_name in [
            "odao_member_leave_event",
            "odao_member_kick_event",
            "sdao_member_leave_event",
            "sdao_member_request_leave_event"
        ]:
            args.nodeAddress = el_explorer_url(args.nodeAddress, block=(event.blockNumber - 1))

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
            # token names
            s = rp.assemble_contract(name="ERC20", address=args.signerToken)
            args.sellToken = s.functions.symbol().call()
            sell_decimals = s.functions.decimals().call()
            b = rp.assemble_contract(name="ERC20", address=args.senderToken)
            args.buyToken = b.functions.symbol().call()
            buy_decimals = b.functions.decimals().call()
            # token amounts
            args.sellAmount = solidity.to_float(args.signerAmount, sell_decimals)
            args.buyAmount = solidity.to_float(args.senderAmount, buy_decimals)
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

        receipt = None
        if "tnx_fee" not in args and cfg["rocketpool.chain"] == "mainnet":
            receipt = w3.eth.get_transaction_receipt(event.transactionHash)
            args.tnx_fee = solidity.to_float(receipt["gasUsed"] * receipt["effectiveGasPrice"])
            args.tnx_fee_dai = rp.get_dai_eth_price() * args.tnx_fee
            args.caller = receipt["from"]

        # add transaction hash and block number to args
        args.transactionHash = event.transactionHash.hex()
        args.blockNumber = event.blockNumber

        # add proposal message manually if the event contains a proposal
        if "pdao_proposal" in event_name:
            proposal_id = event.args.proposalID if "proposalID" in event.args else event.args.proposalId

            if "root" in event_name:
                # not interesting if the root wasn't submitted in response to a challenge
                # ChallengeState.Challenged = 1
                challenge_state = rp.call("rocketDAOProtocolVerifier.getChallengeState", proposal_id, args.index, block=event.blockNumber)
                if challenge_state != 1:
                    return None

            if "add" in event_name or "destroy" in event_name:
                args.proposalBond = solidity.to_int(rp.call("rocketDAOProtocolVerifier.getProposalBond", proposal_id))
            elif "root" in event_name or "challenge" in event_name:
                args.proposalBond = solidity.to_int(rp.call("rocketDAOProtocolVerifier.getProposalBond", proposal_id))
                args.challengeBond = solidity.to_int(rp.call("rocketDAOProtocolVerifier.getChallengeBond", proposal_id))
                args.challengePeriod = rp.call("rocketDAOProtocolVerifier.getChallengePeriod", proposal_id)

            # create human-readable decision for votes
            if "direction" in args:
                args.decision = ["invalid", "abstain", "for", "against", "against with veto"][args.direction]

            if "votingPower" in args:
                args.votingPower = solidity.to_float(args.votingPower)
                if args.votingPower < 250:
                    # not interesting
                    return None
            elif "vote_override" in event_name:
                proposal_block = rp.call("rocketDAOProtocolProposal.getProposalBlock", proposal_id)
                args.votingPower = solidity.to_float(rp.call("rocketNetworkVoting.getVotingPower", args.voter, proposal_block))
                if args.votingPower < 100:
                    # not interesting
                    return None

            proposal = ProtocolDAO.fetch_proposal(proposal_id)
            args.proposal_body = ProtocolDAO().build_proposal_body(
                proposal,
                include_proposer=False,
                include_payload=("add" in event_name),
                include_votes=all(kw not in event_name for kw in ("add", "challenge", "root", "destroy")),
            )
        elif "dao_proposal" in event_name:
            proposal_id = event.args.proposalID

            # create human-readable decision for votes
            if "supported" in args:
                args.decision = "for" if args.supported else "against"

            # change prefix for DAO-specific event
            dao_name = rp.call("rocketDAOProposal.getDAO", proposal_id)
            event_name = event_name.replace("dao", {
                "rocketDAONodeTrustedProposals": "odao",
                "rocketDAOSecurityProposals": "sdao"
            }[dao_name])

            proposal = DefaultDAO.fetch_proposal(proposal_id)
            args.proposal_body = DefaultDAO(dao_name).build_proposal_body(
                proposal,
                include_proposer=False,
                include_payload=("add" in event_name),
                include_votes=("add" not in event_name),
            )

        # store event_name in args
        args.event_name = event_name

        # add inflation and new supply if inflation occurred
        if "rpl_inflation" in event_name:
            args.total_supply = int(solidity.to_float(rp.call("rocketTokenRPL.totalSupply")))
            args.inflation = round(rp.get_annual_rpl_inflation() * 100, 4)

        if "auction_bid_event" in event_name:
            eth = solidity.to_float(args.bidAmount)
            price = solidity.to_float(
                rp.call("rocketAuctionManager.getLotPriceAtBlock", args.lotIndex, args.blockNumber))
            args.rplAmount = eth / price

        if event_name in ["rpl_stake_event", "rpl_withdraw_event"]:
            # get eth price by multiplying the amount by the current RPL ratio
            rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
            args.amount = solidity.to_float(args.amount)
            args.ethAmount = args.amount * rpl_ratio
        if event_name in ["node_merkle_rewards_claimed"]:
            rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
            args.amountRPL = sum(solidity.to_float(r) for r in args.amountRPL)
            args.amountETH = sum(solidity.to_float(e) for e in args.amountETH)
            args.ethAmount = args.amountRPL * rpl_ratio
        if event_name in ["reth_transfer_event"]:
            args.amount = args.value / 10 ** 18

        # reject if the amount is not major
        if any(["reth_transfer_event" in event_name and args.amount < 1000,
                "rpl_stake_event" in event_name and args.amount < 1000,
                "rpl_stake_event" in event_name and args.amount < 1000,
                "node_merkle_rewards_claimed" in event_name and args.ethAmount < 5 and args.amountETH < 5,
                "rpl_withdraw_event" in event_name and args.ethAmount < 16]):
            # "eth_deposit_event" in event_name and args.amount < 32,
            # "eth_withdraw_event" in event_name and args.amount < 32
            amounts = {}
            for arg in ["ethAmount", "amount", "amountETH"]:
                if arg in args:
                    amounts[arg] = args[arg]
            log.debug(f"Skipping {event_name} because the event ({amounts}) is too small to be interesting")
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

        if "minipool_deposit_received_event" in event_name:
            contract = rp.assemble_contract("rocketMinipoolDelegate", args.minipool)
            args.commission = solidity.to_float(contract.functions.getNodeFee().call())
            # get the transaction receipt
            tx = w3.eth.get_transaction(args.transactionHash)
            args.depositAmount = rp.call("rocketMinipool.getNodeDepositBalance", address=args.minipool, block=args.blockNumber)

            if tx["value"] < args.depositAmount and tx["to"] == rp.get_address_by_name("rocketNodeDeposit"):
                receipt = w3.eth.get_transaction_receipt(args.transactionHash)
                args.node = receipt["from"]
                args.creditAmount = args.depositAmount - tx["value"]
                args.balanceAmount = 0

                event = rp.get_contract_by_name("rocketVault").events.EtherWithdrawn()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    processed_logs = event.processReceipt(receipt)

                deposit_contract = bytes(w3.soliditySha3(["string"], ["rocketNodeDeposit"]))
                for withdraw_event in processed_logs:
                    if withdraw_event.args["by"] == deposit_contract:
                        args.balanceAmount = withdraw_event.args["amount"]
                        args.creditAmount -= args.balanceAmount
                        break

                if args.balanceAmount == 0:
                    args.event_name += "_credit"
                elif args.creditAmount == 0:
                    args.event_name += "_balance"
                else:
                    args.event_name += "_shared"

        if event_name in ["minipool_bond_reduce_event", "minipool_vacancy_prepared_event",
                          "minipool_withdrawal_processed_event", "minipool_bond_reduction_started_event",
                          "pool_deposit_assigned_event"]:
            # get the node operator address from minipool contract
            contract = rp.assemble_contract("rocketMinipool", args.minipool)
            args.node = contract.functions.getNodeAddress().call()
        if "minipool_bond_reduction_started_event" in event_name:
            # get the previousBondAmount from the minipool contract
            args.previousBondAmount = solidity.to_float(
                rp.call("rocketMinipool.getNodeDepositBalance", address=args.minipool, block=args.blockNumber - 1))
        elif event_name == "minipool_withdrawal_processed_event":
            args.totalAmount = args.nodeAmount + args.userAmount
        elif event_name == "pool_deposit_assigned_event":
            if event["assignment_count"] == 1:
                args.event_name = "pool_deposit_assigned_single_event"
            elif event["assignment_count"] > 1:
                args.assignmentCount = event["assignment_count"]
            else:
                return None
        elif "minipool_scrub" in event_name and rp.call("rocketMinipoolDelegate.getVacant", address=args.minipool):
            args.event_name = f"vacant_{event_name}"
            if args.event_name == "vacant_minipool_scrub_event":
                # let's try to determine the reason. there are 4 reasons a vacant minipool can get scrubbed:
                # 1. the validator does not have the withdrawal credentials set to the minipool address, but to some other address
                # 2. the validator balance on the beacon chain is lower than configured in the minipool contract
                # 3. the validator does not have the active_ongoing validator status
                # 4. the migration could have timed out, the oDAO will scrub minipools after they have passed half of the migration window
                # get pubkey from minipool contract
                pubkey = rp.call("rocketMinipoolManager.getMinipoolPubkey", args.minipool, block=args.blockNumber - 1).hex()
                vali_info = bacon.get_validator(f"0x{pubkey}")["data"]
                reason = "joe fucking up (unknown reason)"
                if vali_info:
                    # check for #1
                    if all([vali_info["validator"]["withdrawal_credentials"][:4] == "0x01",
                           vali_info["validator"]["withdrawal_credentials"][-40:] != args.minipool[2:]]):
                        reason = "having invalid withdrawal credentials set on the beacon chain"
                    # check for #2
                    configured_balance = solidity.to_float(
                        rp.call("rocketMinipoolDelegate.getPreMigrationBalance", address=args.minipool,
                                block=args.blockNumber - 1))
                    if (solidity.to_float(vali_info["balance"], 9) - configured_balance) < -0.01:
                        reason = "having a balance lower than configured in the minipool contract on the beacon chain"
                    # check for #3
                    if vali_info["status"] != "active_ongoing":
                        reason = "not being active on the beacon chain"
                    # check for #4
                    scrub_period = rp.call("rocketDAONodeTrustedSettingsMinipool.getPromotionScrubPeriod",
                                           block=args.blockNumber - 1)
                    minipool_creation = rp.call("rocketMinipoolDelegate.getStatusTime", address=args.minipool,
                                                block=args.blockNumber - 1)
                    block_time = w3.eth.getBlock(args.blockNumber - 1)["timestamp"]
                    if block_time - minipool_creation > scrub_period // 2:
                        reason = "taking too long to migrate their withdrawal credentials on the beacon chain"
                args.scrub_reason = reason

        if "unsteth_withdrawal_requested_event" in event_name:
            if receipt:
                args.timestamp = w3.eth.getBlock(receipt["blockNumber"])["timestamp"]
            if solidity.to_float(args.amountOfStETH) < 10_000:
                return None
            # get the node operator address from minipool contract
        if "rpl_transfer_event" in event_name:
            if args["from"] not in cfg["dao_multsigs"]:
                return None
        args = prepare_args(args)
        return assemble(args)

    def run_loop(self):
        if self.state == "RUNNING":
            log.error("Boostrap plugin was interrupted while running. Re-initializing...")
            self.__init__(self.bot)
        return self.check_for_new_events()

    def aggregate_events(self, events: list[Union[LogReceipt, EventData]]) -> list[aDict]:
        # aggregate and deduplicate events within the same transaction
        events_by_tx = {}
        for event in reversed(events):
            tx_hash = event["transactionHash"]
            if tx_hash not in events_by_tx:
                events_by_tx[tx_hash] = []

            events_by_tx[tx_hash].append(event)

        aggregation_attributes = {
            "rocketDepositPool.DepositAssigned": "assignment_count",
            "unstETH.WithdrawalRequested": "amountOfStETH"
        }

        def get_event_name(_event: Union[LogReceipt, EventData]) -> tuple[str, str]:
            if "topics" in _event:
                contract_name = rp.get_name_by_address(_event["address"])
                name = self.topic_mapping[_event["topics"][0].hex()]
            else:
                contract_name = None
                name = _event.get("event")

            full_name = f"{contract_name}.{name}" if contract_name else name
            return name, full_name

        aggregates = {}
        for tx_hash, tx_events in events_by_tx.items():
            tx_aggregates = {}
            aggregates[tx_hash] = tx_aggregates
            events_by_name: dict[str, list[Union[LogReceipt, EventData]]] = {}

            for event in tx_events:
                event_name, full_event_name = get_event_name(event)

                if full_event_name not in events_by_name:
                    events_by_name[full_event_name] = []

                if full_event_name == "unstETH.WithdrawalRequested":
                    contract = rp.get_contract_by_address(event["address"])
                    _event = aDict(contract.events[event_name]().processLog(event))
                    # sum up the amount of stETH withdrawn in this transaction
                    if amount := tx_aggregates.get(full_event_name, 0):
                        events.remove(event)
                    tx_aggregates[full_event_name] = amount + _event["args"]["amountOfStETH"]
                elif full_event_name == "rocketTokenRETH.Transfer":
                    if "rocketTokenRETH.TokensBurned" in tx_aggregates:
                        events.remove(event)
                        continue
                    if prev_event := tx_aggregates.get(full_event_name, None):
                        contract = rp.get_contract_by_address(event["address"])
                        _event = aDict(contract.events[event_name]().processLog(event))
                        _prev_event = aDict(contract.events[event_name]().processLog(event))
                        if _prev_event["args"]["value"] > _event["args"]["value"]:
                            events.remove(event)
                            event = prev_event
                        else:
                            events.remove(prev_event)
                    tx_aggregates[full_event_name] = event
                elif full_event_name == "rocketDAOProtocolProposal.ProposalVoteOverridden":
                    # override is emitted first, thus only seen here after the main vote event
                    # remove last seen vote event
                    vote_event = events_by_name.get("rocketDAOProtocolProposal.ProposalVoted", [None]).pop()
                    if vote_event is not None:
                        events.remove(vote_event)
                elif full_event_name == "MinipoolPrestaked":
                    assign_event = events_by_name.get("rocketDepositPool.DepositAssigned", [None]).pop()
                    if assign_event is not None:
                        events.remove(assign_event)
                        tx_aggregates["rocketDepositPool.DepositAssigned"] -= 1
                elif full_event_name in aggregation_attributes:
                    # there is a special aggregated event, remove duplicates
                    if count := tx_aggregates.get(full_event_name, 0):
                        events.remove(event)
                    tx_aggregates[full_event_name] = count + 1
                else:
                    # count, but report as individual events
                    tx_aggregates[full_event_name] = tx_aggregates.get(full_event_name, 0) + 1

                if event in events:
                    events_by_name[full_event_name].append(event)

        events = [aDict(event) for event in events]
        for event in events:
            _, full_event_name = get_event_name(event)
            if full_event_name not in aggregation_attributes:
                continue

            tx_hash = event["transactionHash"]
            if (aggregated_value := aggregates[tx_hash].get(full_event_name, None)) is None:
                continue

            event[aggregation_attributes[full_event_name]] = aggregated_value

        return events

    def check_for_new_events(self):
        log.info("Checking for new events")

        do_full_check = self.state == "INIT"
        if do_full_check:
            log.info("Doing full check")
        self.state = "RUNNING"

        pending_events = []

        for events in self.events:
            if do_full_check:
                pending_events += events.get_all_entries()
            else:
                pending_events += events.get_new_entries()
        log.debug(f"Found {len(pending_events)} pending events")

        should_reinit, messages = self.process_events(pending_events)
        log.debug("Finished checking for new events")
        # store last checked block in db if it's bigger than the one we have stored
        self.state = "OK"
        self.db.last_checked_block.replace_one({"_id": "events"}, {"_id": "events", "block": self.start_block},
                                               upsert=True)
        if should_reinit:
            log.info("Detected update, triggering reinit")
            self.state = "RUNNING"
        return messages

    def process_events(self, events: list[EventData]) -> tuple[bool, list[Response]]:
        events.sort(key=lambda e: (e.blockNumber, e.logIndex))
        messages = []
        should_reinit = False

        for event in self.aggregate_events(events):
            tnx_hash = event.transactionHash.hex()
            embed = None
            event_name = None

            if event.get("removed", False):
                continue

            log.debug(f"Checking Event {event}")

            address = event.address
            if (n := rp.get_name_by_address(address)) and "topics" in event:
                log.info(f"Found event {event} for {n}")
                # default event path
                contract = rp.get_contract_by_address(address)
                contract_event = self.topic_mapping[event.topics[0].hex()]
                topics = [w3.toHex(t) for t in event.topics]
                _event = aDict(contract.events[contract_event]().processLog(event))
                _event.topics = topics
                if "assignment_count" in event:
                    _event.assignment_count = event.assignment_count
                if "amountOfStETH" in event:
                    _event.args = aDict(_event.args)
                    _event.args.amountOfStETH = event.amountOfStETH
                event = _event

                if event_name := self.internal_event_mapping.get(f"{n}.{event.event}", None):
                    embed = self.create_embed(event_name, event)
                else:
                    log.warning(f"Skipping unknown event {n}.{event.event}")

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
                unique_id = f"{tnx_hash}:{event_name}"
                for arg_k, arg_v in event.get("args", {}).items():
                    if all(t not in arg_k.lower() for t in ["time", "block", "timestamp"]):
                        unique_id += f":{arg_k}:{arg_v}"

                # get the event offset based on the lowest event log index of events with the same txn hashes and block hashes
                log_index_offset = min(e.logIndex for e in events if e.transactionHash == event.transactionHash and e.blockHash == event.blockHash)
                unique_id += f":{event.logIndex - log_index_offset}"
                messages.append(Response(
                    embed=embed,
                    topic="events",
                    event_name=event_name,
                    unique_id=unique_id,
                    block_number=event.blockNumber,
                    transaction_index=event.transactionIndex,
                    event_index=event.logIndex
                ))
            if event.blockNumber > self.start_block and not should_reinit:
                self.start_block = event.blockNumber

        return should_reinit, messages


async def setup(bot):
    await bot.add_cog(QueuedEvents(bot))
