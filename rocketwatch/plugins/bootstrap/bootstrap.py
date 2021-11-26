import json
import logging

import web3.exceptions
from cachetools import FIFOCache
from discord.ext import commands, tasks
from web3.datastructures import MutableAttributeDict as aDict

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args, exception_fallback
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.shared_w3 import w3

log = logging.getLogger("bootstrap")
log.setLevel(cfg["log_level"])

DEPOSIT_EVENT = 2
WITHDRAWABLE_EVENT = 3


class Bootstrap(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = "OK"
        self.tnx_hash_cache = FIFOCache(maxsize=256)
        self.addresses = []
        self.internal_function_mapping = {}

        self.block_event = w3.eth.filter("latest")

        with open("./plugins/bootstrap/functions.json") as f:
            mapped_events = json.load(f)

        for contract_name, event_mapping in mapped_events.items():
            self.addresses.append(rp.get_address_by_name(contract_name))
            self.internal_function_mapping[contract_name] = event_mapping

        if not self.run_loop.is_running():
            self.run_loop.start()

    @exception_fallback()
    async def create_embed(self, event_name, event):
        # prepare args
        args = aDict(event.args)

        # store event_name in args
        args.event_name = event_name

        # add transaction hash and block number to args
        args.transactionHash = event.hash.hex()
        args.blockNumber = event.blockNumber

        if "dao_disable" in event_name and not event.confirmDisableBootstrapMode:
            return None

        if "deposit" in event_name:
            receipt = w3.eth.get_transaction_receipt(args.transactionHash)
            args.burnedValue = solidity.to_float(event.gasPrice * receipt.gasUsed)
            args.node = receipt["from"]

        if "SettingBool" in args.function_name:
            args.value = bool(args.value)

        if event_name == "bootstrap_pdao_multi":
            description_parts = []
            for i in range(len(args.settingContractNames)):
                # these are the only types rocketDAOProtocolProposals checks, so fine to hard code until further changes
                # SettingType.UINT256
                if args.types[i] == 0:
                    value = w3.toInt(args.data[i])
                # SettingType.BOOL
                elif args.types[i] == 1:
                    value = bool(args.data[i])
                # SettingType.ADDRESS
                elif args.types[i] == 3:
                    value = w3.toChecksumAddress(args.data[i])
                else:
                    value = "???"
                description_parts.append(
                    f"`{args.settingContractNames[i]}`: `{args.settingsPath[i]}` set to `{value}`!"
                )
            args.description = "\n".join(description_parts)

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
        return Response(
            embed=assemble(args),
            event_name=event_name)

    @tasks.loop(seconds=30.0)
    async def run_loop(self):
        if self.state == "STOPPED":
            return

        if self.state != "ERROR":
            try:
                self.state = "OK"
                return await self.check_for_new_transactions()
            except Exception as err:
                self.state = "ERROR"
                await report_error(err)
        try:
            return self.__init__(self.bot)
        except Exception as err:
            await report_error(err)

    async def check_for_new_transactions(self):
        log.info("Checking for new Bootstrap Commands")

        messages = []
        for block_hash in reversed(list(self.block_event.get_new_entries())):
            log.debug(f"Checking Block: {block_hash.hex()}")
            try:
                block = w3.eth.get_block(block_hash, full_transactions=True)
            except web3.exceptions.BlockNotFound:
                log.error(f"Skipping Block {block_hash.hex()} as it can't be found")
                continue
            for tnx in block.transactions:
                if tnx.hash in self.tnx_hash_cache:
                    continue
                if "to" not in tnx:
                    # probably a contract creation transaction
                    log.debug(f"Skipping Transaction {tnx.hash.hex()} as it has no `to` parameter. Possible Contract Creation.")
                    continue
                if tnx.to in self.addresses:
                    self.tnx_hash_cache[tnx.hash] = True
                    contract_name = rp.get_name_by_address(tnx.to)

                    # get receipt and check if the transaction reverted using status attribute
                    receipt = w3.eth.get_transaction_receipt(tnx.hash)
                    if contract_name == "rocketNodeDeposit" and receipt.status:
                        log.info(f"Skipping Successful Node Deposit {tnx.hash.hex()}")
                        continue
                    if contract_name != "rocketNodeDeposit" and not receipt.status:
                        log.info(f"Skipping Reverted Bootstrap Call {tnx.hash.hex()}")
                        continue

                    contract = rp.get_contract_by_address(tnx.to)

                    decoded = contract.decode_function_input(tnx.input)
                    log.debug(decoded)

                    function = decoded[0].function_identifier
                    event_name = self.internal_function_mapping[contract_name].get(function, None)

                    if event_name:
                        event = aDict(tnx)
                        event.args = {}
                        for arg, value in decoded[1].items():
                            event.args[arg.lstrip("_")] = value
                        event.args["timestamp"] = block.timestamp
                        event.args["function_name"] = function

                        result = await self.create_embed(event_name, event)

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

        log.debug("Finished Checking for new Bootstrap Commands")

        if messages:
            log.info(f"Sending {len(messages)} Message(s)")

            channels = cfg["discord.channels"]

            for message in sorted(messages, key=lambda a: a["score"], reverse=False):
                log.debug(f"Sending \"{message.result.event_name}\" Event")
                channel_candidates = [value for key, value in channels.items() if message.result.event_name.startswith(key)]
                channel = await self.bot.fetch_channel(channel_candidates[0] if channel_candidates else channels['default'])
                await channel.send(embed=message.result.embed)

            log.info("Finished sending Message(s)")

    def cog_unload(self):
        self.state = "STOPPED"
        self.run_loop.cancel()


def setup(bot):
    bot.add_cog(Bootstrap(bot))
