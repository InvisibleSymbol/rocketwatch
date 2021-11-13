import logging
import os
import warnings

from bidict import bidict
from cachetools import cached

from utils import solidity
from utils.cfg import cfg
from utils.readable import decode_abi
from utils.shared_w3 import w3

log = logging.getLogger("rocketpool")
log.setLevel(cfg["log_level"])


class RocketPool:
    addresses = bidict()

    def __init__(self):
        storage_address = cfg['rocketpool.storage_contract']
        self.storage_contract = self.assemble_contract("rocketStorage", storage_address)
        self.addresses["rocketStorage"] = storage_address

    @cached(cache={})
    def get_address_by_name(self, name):
        return self.uncached_get_address_by_name(name)

    def uncached_get_address_by_name(self, name):
        log.debug(f"Retrieving address for {name} Contract")
        sha3 = w3.soliditySha3(["string", "string"], ["contract.address", name])
        address = self.storage_contract.functions.getAddress(sha3).call()
        self.addresses[name] = address
        return address

    @cached(cache={})
    def get_abi_by_name(self, name):
        return self.uncached_get_abi_by_name(name)

    def uncached_get_abi_by_name(self, name):
        log.debug(f"Retrieving abi for {name} Contract")
        sha3 = w3.soliditySha3(["string", "string"], ["contract.abi", name])
        compressed_string = self.storage_contract.functions.getString(sha3).call()
        return decode_abi(compressed_string)

    @cached(cache={})
    def assemble_contract(self, name, address=None):
        abi = None

        if os.path.exists(f"./contracts/{name}.abi"):
            with open(f"./contracts/{name}.abi", "r") as f:
                abi = f.read()
        if not abi:
            abi = self.get_abi_by_name(name)
        if not abi:
            raise Exception(f"No abi found for {name} Contract")
        return w3.eth.contract(address=address, abi=abi)

    def get_name_by_address(self, address):
        return self.addresses.inverse.get(address, None)

    def get_contract_by_name(self, name):
        address = self.get_address_by_name(name)
        if not address:
            raise Exception(f"No address found for {name} Contract")
        return self.assemble_contract(name, address)

    def get_contract_by_address(self, address):
        """
        **WARNING**: only call after contract has been previously retrieved using its name
        """
        name = self.get_name_by_address(address)
        return self.assemble_contract(name, address)

    def call(self, path, *args):
        parts = path.split(".")
        if len(parts) != 2:
            raise Exception(f"Invalid contract path: Invalid part count: have {len(parts)}, want 2")
        name, function = parts
        contract = self.get_contract_by_name(name)
        return contract.functions[function](*args).call()

    def get_pubkey_using_transaction(self, receipt):
        # will throw some warnings about other events but those are safe to ignore since we don't need those anyways
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            processed_logs = self.get_contract_by_name("casperDeposit").events.DepositEvent().processReceipt(receipt)

        # attempt to retrieve the pubkey
        if processed_logs:
            deposit_event = processed_logs[0]
            return deposit_event.args.pubkey.hex()

    def get_annual_rpl_inflation(self):
        inflation_per_interval = solidity.to_float(self.call("rocketTokenRPL.getInflationIntervalRate"))
        if not inflation_per_interval:
            return 0
        seconds_per_interval = self.call("rocketTokenRPL.getInflationIntervalTime")
        intervals_per_year = solidity.years / seconds_per_interval
        return (inflation_per_interval ** intervals_per_year) - 1

    def get_percentage_rpl_swapped(self):
        value = solidity.to_float(self.call("rocketTokenRPL.totalSwappedRPL"))
        percentage = (value / 18_000_000) * 100
        return round(percentage, 2)


rp = RocketPool()
