import json
import logging
import warnings

import web3.exceptions
from discord import Object
from discord.app_commands import guilds
from discord.ext.commands import Cog, Context, is_owner, hybrid_command
from web3.datastructures import MutableAttributeDict as aDict

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.dao import DefaultDAO, ProtocolDAO

log = logging.getLogger("transactions")
log.setLevel(cfg["log_level"])


class QueuedTransactions(Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = "INIT"
        self.addresses = []
        self.internal_function_mapping = {}

        self.block_event = w3.eth.filter("latest")

        with open("./plugins/transactions/functions.json") as f:
            mapped_events = json.load(f)

        for contract_name, event_mapping in mapped_events.items():
            try:
                address = rp.get_address_by_name(contract_name)
            except Exception as e:
                log.exception(e)
                log.error(f"Could not find address for contract {contract_name}")
                continue
            self.addresses.append(address)
            self.internal_function_mapping[contract_name] = event_mapping

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
    ):
        await ctx.defer()
        try:
            event_obj = aDict({
                "hash": aDict({"hex": lambda: '0x0000000000000000000000000000000000000000'}),
                "blockNumber": block_number,
                "args": json.loads(json_args) | {"function_name": function}
            })
        except json.JSONDecodeError:
            return await ctx.send(content="Invalid JSON args!")

        event_name = self.internal_function_mapping[contract][function]
        if embed := self.create_embed(event_name, event_obj):
            await ctx.send(embed=embed)
        else:
            await ctx.send(content="<empty>")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def replay_tx(self, ctx: Context, tx_hash: str):
        await ctx.defer()
        tnx = w3.eth.get_transaction(tx_hash)
        block = w3.eth.get_block(tnx.blockHash)

        responses: list[Response] = self.process_transaction(block, tnx, tnx.to, tnx.input)
        for response in responses:
            await ctx.send(embed=response.embed)

    @staticmethod
    def create_embed(event_name, event):
        # prepare args
        args = aDict(event.args)

        # store event_name in args
        args.event_name = event_name

        # add transaction hash and block number to args
        args.transactionHash = event.hash.hex()
        args.blockNumber = event.blockNumber

        # oDAO bootstrap doesn't emit an event
        if "odao_disable" in event_name and not args.confirmDisableBootstrapMode:
            return None

        if "failed_deposit" in event_name:
            receipt = w3.eth.get_transaction_receipt(args.transactionHash)
            args.node = receipt["from"]
            args.burnedValue = solidity.to_float(event.gasPrice * receipt.gasUsed)
        elif "deposit_pool_queue" in event_name:
            receipt = w3.eth.get_transaction_receipt(args.transactionHash)
            args.node = receipt["from"]
            event = rp.get_contract_by_name("rocketMinipoolQueue").events.MinipoolDequeued()
            # get the amount of dequeues that happened in this transaction using the event logs
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                processed_logs = event.processReceipt(receipt)
            args.count = len(processed_logs)

        if "SettingBool" in args.function_name:
            args.value = bool(args.value)

        # this is duplicated for now because boostrap events are in events.py
        # and there is no good spot in utils for it
        if event_name == "pdao_claimer":
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

        if event_name == "sdao_member_kick":
            args.id = rp.call("rocketDAOSecurity.getMemberID", args.memberAddress, block=event.blockNumber - 1)
        elif event_name == "sdao_member_replace":
            args.existing_id = rp.call("rocketDAOSecurity.getMemberID", args.existingMemberAddress, block=event.blockNumber - 1)
        elif event_name == "sdao_member_kick_multi":
            member_list = []
            for member_address in args.memberAddresses:
                member_id = rp.call("rocketDAOSecurity.getMemberID", member_address, block=event.blockNumber - 1)
                member_list.append(f"**{member_id}** (`{member_address}`)")
            args.member_list = "\n".join(member_list)

        if event_name == "bootstrap_odao_network_upgrade":
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

    def process_transaction(self, block, tnx, contract_address, fn_input) -> list[Response]:
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

        response = Response(
            topic="transactions",
            embed=embed,
            event_name=event_name,
            unique_id=f"{tnx.hash.hex()}:{event_name}",
            block_number=event.blockNumber,
            transaction_index=event.transactionIndex
        )

        return [response] + responses

    def run_loop(self):
        if self.state == "RUNNING":
            log.error("Transaction plugin was interrupted while running. Re-initializing...")
            self.__init__(self.bot)
        return self.check_for_new_transactions()

    def check_for_new_transactions(self):
        log.info("Checking for new Transaction Commands")
        payload = []

        do_full_check = self.state == "INIT"
        self.state = "RUNNING"
        if do_full_check:
            log.info("Doing full check")
            latest_block = w3.eth.getBlock("latest").number
            blocks = list(range(latest_block - cfg["core.look_back_distance"], latest_block))
        else:
            blocks = list(self.block_event.get_new_entries())

        for block_hash in blocks:
            log.debug(f"Checking block: {block_hash}")
            try:
                block = w3.eth.get_block(block_hash, full_transactions=True)
            except web3.exceptions.BlockNotFound:
                log.error(f"Skipping block {block_hash} as it can't be found")
                continue

            for tnx in block.transactions:
                if "to" in tnx:
                    payload.extend(self.process_transaction(block, tnx, tnx.to, tnx.input))
                else:
                    # probably a contract creation transaction
                    log.debug(f"Skipping Transaction {tnx.hash.hex()} as it has no `to` parameter. Possible Contract Creation.")

        self.state = "OK"
        return payload


async def setup(bot):
    await bot.add_cog(QueuedTransactions(bot))
