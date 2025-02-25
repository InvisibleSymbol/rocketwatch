import json
import logging
import warnings
from typing import cast, Optional

import web3.exceptions
from discord import Object
from discord.app_commands import guilds
from discord.ext.commands import Context, is_owner, hybrid_command
from eth_typing import ChecksumAddress, BlockNumber, BlockIdentifier
from web3.datastructures import MutableAttributeDict as aDict

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.dao import DefaultDAO, ProtocolDAO
from utils.embeds import assemble, prepare_args, el_explorer_url, Embed
from utils.event import EventPlugin, Event
from utils.rocketpool import rp
from utils.shared_w3 import w3

log = logging.getLogger("transactions")
log.setLevel(cfg["log_level"])


class Transactions(EventPlugin):
    def __init__(self, bot: RocketWatch):
        super().__init__(bot)

        with open("./plugins/transactions/functions.json") as f:
            tx_config = json.load(f)
            contract_addresses, function_map = self._parse_transaction_config(tx_config)
            self.addresses = contract_addresses
            self.internal_function_mapping = function_map

    @staticmethod
    def _parse_transaction_config(tx_config: dict) -> tuple[list[ChecksumAddress], dict]:
        addresses: list[ChecksumAddress] = []
        function_map = {}

        for contract_name, mapping in tx_config.items():
            try:
                address = rp.get_address_by_name(contract_name)
                addresses.append(address)
                function_map[contract_name] = mapping
            except Exception:
                log.exception(f"Could not find address for contract {contract_name}")

        return addresses, function_map

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def trigger_tx(
            self,
            ctx: Context,
            contract: str,
            function: str,
            json_args: str = "{}",
            block_number: int = 0
    ) -> None:
        await ctx.defer()
        try:
            event_obj = aDict({
                "hash": aDict({"hex": lambda: '0x0000000000000000000000000000000000000000'}),
                "blockNumber": block_number,
                "args": json.loads(json_args) | {"function_name": function}
            })
        except json.JSONDecodeError:
            await ctx.send(content="Invalid JSON args!")
            return

        event_name = self.internal_function_mapping[contract][function]
        if embed := self.create_embed(event_name, event_obj):
            await ctx.send(embed=embed)
        else:
            await ctx.send(content="No events triggered.")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def replay_tx(self, ctx: Context, tx_hash: str):
        await ctx.defer()
        tnx = w3.eth.get_transaction(tx_hash)
        block = w3.eth.get_block(tnx.blockHash)

        responses: list[Event] = self.process_transaction(block, tnx, tnx.to, tnx.input)
        if not responses:
            await ctx.send(content="No events found.")

        for response in responses:
            await ctx.send(embed=response.embed)

    def _get_new_events(self) -> list[Event]:
        from_block = self.last_served_block + 1 - self.lookback_distance
        return self._get_past_events(from_block, self._pending_block)

    def _get_past_events(self, from_block: BlockNumber, to_block: BlockNumber) -> list[Event]:
        events = []
        for block in range(from_block, to_block):
            events.extend(self.get_events_for_block(cast(BlockNumber, block)))
        return events

    def get_events_for_block(self, block_number: BlockIdentifier) -> list[Event]:
        log.debug(f"Checking block {block_number}")
        try:
            block = w3.eth.get_block(block_number, full_transactions=True)
        except web3.exceptions.BlockNotFound:
            log.error(f"Skipping block {block_number} as it can't be found")
            return []

        for tnx in block.transactions:
            if "to" in tnx:
                return self.process_transaction(block, tnx, tnx.to, tnx.input)
            else:
                # probably a contract creation transaction
                log.debug(f"Skipping transaction {tnx.hash.hex()} as it has no `to` parameter. Possible contract creation.")

        return []

    @staticmethod
    def create_embed(event_name: str, event: aDict) -> Optional[Embed]:
        # prepare args
        args = aDict(event.args)

        # store event_name in args
        args.event_name = event_name

        # add transaction hash and block number to args
        args.transactionHash = event.hash.hex()
        args.blockNumber = event.blockNumber

        receipt = w3.eth.get_transaction_receipt(args.transactionHash)

        # oDAO bootstrap doesn't emit an event
        if "odao_disable" in event_name and not args.confirmDisableBootstrapMode:
            return None
        elif event_name == "pdao_set_delegate":
            args.delegator = receipt["from"]
            args.delegate = args.get("delegate") or args.get("newDelegate")
            args.votingPower = solidity.to_float(rp.call("rocketNetworkVoting.getVotingPower", args.delegator, args.blockNumber))
            if (args.votingPower < 100) or (args.delegate == args.delegator):
                return None
        elif "failed_deposit" in event_name:
            args.node = receipt["from"]
            args.burnedValue = solidity.to_float(event.gasPrice * receipt.gasUsed)
        elif "deposit_pool_queue" in event_name:
            args.node = receipt["from"]
            event = rp.get_contract_by_name("rocketMinipoolQueue").events.MinipoolDequeued()
            # get the amount of dequeues that happened in this transaction using the event logs
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                processed_logs = event.processReceipt(receipt)
            args.count = len(processed_logs)
        elif "SettingBool" in args.function_name:
            args.value = bool(args.value)
        # this is duplicated for now because boostrap events are in events.py
        # and there is no good spot in utils for it
        elif event_name == "pdao_claimer":
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
        elif event_name == "pdao_setting_multi":
            description_parts = []
            for i in range(len(args.settingContractNames)):
                value_raw = args.data[i]
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
        elif event_name == "sdao_member_kick":
            args.memberAddress = el_explorer_url(args.memberAddress, block=(event.blockNumber - 1))
        elif event_name == "sdao_member_replace":
            args.existingMemberAddress = el_explorer_url(args.existingMemberAddress, block=(event.blockNumber - 1))
        elif event_name == "sdao_member_kick_multi":
            args.member_list = ", ".join([
                el_explorer_url(member_address, block=(event.blockNumber - 1))
                for member_address in args.memberAddresses
            ])
        elif event_name == "bootstrap_odao_network_upgrade":
            if args.type == "addContract":
                args.description = f"Contract `{args.name}` has been added!"
            elif args.type == "upgradeContract":
                args.description = f"Contract `{args.name}` has been upgraded!"
            elif args.type == "addABI":
                args.description = f"[ABI](https://ethereum.org/en/glossary/#abi) for Contract `{args.name}` has been added!"
            elif args.type == "upgradeABI":
                args.description = f"[ABI](https://ethereum.org/en/glossary/#abi) of Contract `{args.name}` has been upgraded!"
            else:
                raise Exception(f"Network Upgrade of type {args.type} is not known.")

        args = prepare_args(args)
        return assemble(args)

    def process_transaction(self, block, tnx, contract_address, fn_input) -> list[Event]:
        if contract_address not in self.addresses:
            return []

        contract_name = rp.get_name_by_address(contract_address)
        # get receipt and check if the transaction reverted using status attribute
        receipt = w3.eth.get_transaction_receipt(tnx.hash)
        if contract_name == "rocketNodeDeposit" and receipt.status:
            log.info(f"Skipping successful node deposit {tnx.hash.hex()}")
            return []

        if contract_name != "rocketNodeDeposit" and not receipt.status:
            log.info(f"Skipping reverted transaction {tnx.hash.hex()}")
            return []

        try:
            contract = rp.get_contract_by_address(contract_address)
            decoded = contract.decode_function_input(fn_input)
        except ValueError:
            log.error(f"Skipping transaction {tnx.hash.hex()} as it has invalid input")
            return []
        log.debug(decoded)

        function = decoded[0].function_identifier
        if (event_name := self.internal_function_mapping[contract_name].get(function)) is None:
            return []

        event = aDict(tnx)
        event.args = {arg.lstrip("_"): value for arg, value in decoded[1].items()}
        event.args["timestamp"] = block.timestamp
        event.args["function_name"] = function
        if not receipt.status:
            event.args["reason"] = rp.get_revert_reason(tnx)
            # if revert reason includes the phrase "insufficient for pre deposit" filter out
            if "insufficient for pre deposit" in event.args["reason"]:
                log.info(f"Skipping Insufficient Pre Deposit {tnx.hash.hex()}")
                return []

        if event_name == "dao_proposal_execute":
            dao_name = rp.call("rocketDAOProposal.getDAO", event.args["proposalID"])
            # change prefix for DAO-specific event
            event_name = event_name.replace("dao", {
                "rocketDAONodeTrustedProposals": "odao",
                "rocketDAOSecurityProposals": "sdao"
            }[dao_name])

        responses = []

        # proposal being executed, this will call another function
        # use proposal payload to generate second event if applicable
        if "dao_proposal_execute" in event_name:
            proposal_id = event.args["proposalID"]
            if "pdao" in event_name:
                dao = ProtocolDAO()
                payload = rp.call("rocketDAOProtocolProposal.getPayload", proposal_id)
            else:
                dao = DefaultDAO(rp.call("rocketDAOProposal.getDAO", proposal_id))
                payload = rp.call("rocketDAOProposal.getPayload", proposal_id)

            event.args["executor"] = event["from"]
            proposal = dao.fetch_proposal(proposal_id)
            event.args["proposal_body"] = dao.build_proposal_body(proposal, include_proposer=False)

            dao_address = dao.contract.address
            responses = self.process_transaction(block, tnx, dao_address, payload)

        if (embed := self.create_embed(event_name, event)) is None:
            return responses

        response = Event(
            topic="transactions",
            embed=embed,
            event_name=event_name,
            unique_id=f"{tnx.hash.hex()}:{event_name}",
            block_number=event.blockNumber,
            transaction_index=event.transactionIndex,
            event_index=(999 - len(responses)),
        )
        return [response] + responses

async def setup(bot):
    await bot.add_cog(Transactions(bot))
