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
        for name, address in cfg["rocketpool.manual_addresses"].items():
            self.addresses[name] = address

    @cached(cache={})
    def get_address_by_name(self, name):
        # manual overwrite at init
        if name in self.addresses:
            return self.addresses[name]
        return self.uncached_get_address_by_name(name)

    def uncached_get_address_by_name(self, name):
        log.debug(f"Retrieving address for {name} Contract")
        sha3 = w3.soliditySha3(["string", "string"], ["contract.address", name])
        address = self.get_contract_by_name("rocketStorage").functions.getAddress(sha3).call()
        if not w3.toInt(hexstr=address):
            raise Exception(f"No address found for {name} Contract")
        self.addresses[name] = address
        return address

    @cached(cache={})
    def get_abi_by_name(self, name):
        return self.uncached_get_abi_by_name(name)

    def uncached_get_abi_by_name(self, name):
        log.debug(f"Retrieving abi for {name} Contract")
        sha3 = w3.soliditySha3(["string", "string"], ["contract.abi", name])
        compressed_string = self.get_contract_by_name("rocketStorage").functions.getString(sha3).call()
        if not compressed_string:
            raise Exception(f"No abi found for {name} Contract")
        return decode_abi(compressed_string)

    @cached(cache={})
    def assemble_contract(self, name, address=None):
        abi = None

        if os.path.exists(f"./contracts/{name}.abi"):
            with open(f"./contracts/{name}.abi", "r") as f:
                abi = f.read()
        if not abi:
            abi = self.get_abi_by_name(name)
        return w3.eth.contract(address=address, abi=abi)

    def get_name_by_address(self, address):
        return self.addresses.inverse.get(address, None)

    def get_contract_by_name(self, name):
        address = self.get_address_by_name(name)
        return self.assemble_contract(name, address)

    def get_contract_by_address(self, address):
        """
        **WARNING**: only call after contract has been previously retrieved using its name
        """
        name = self.get_name_by_address(address)
        return self.assemble_contract(name, address)

    def estimate_gas_for_call(self, path, *args, block="latest"):
        log.debug(f"Estimating gas for {path} (block={block})")
        parts = path.split(".")
        if len(parts) != 2:
            raise Exception(f"Invalid contract path: Invalid part count: have {len(parts)}, want 2")
        name, function = parts
        contract = self.get_contract_by_name(name)
        return contract.functions[function](*args).estimateGas({"gas": 2 ** 32},
                                                               block_identifier=block)

    def call(self, path, *args, block="latest", address=None):
        log.debug(f"Calling {path} (block={block})")
        parts = path.split(".")
        if len(parts) != 2:
            raise Exception(f"Invalid contract path: Invalid part count: have {len(parts)}, want 2")
        name, function = parts
        if not address:
            address = self.get_address_by_name(name)
        contract = self.assemble_contract(name, address)
        return contract.functions[function](*args).call(block_identifier=block)

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

    def get_minipools_by_type(self, minipool_type, limit=10):
        key = w3.soliditySha3(["string"], [minipool_type])
        cap = self.call("addressQueueStorage.getLength", key)
        limit = min(cap, limit)
        results = []
        for i in range(0, limit):
            results.append(self.call("addressQueueStorage.getItem", key, i))
        return cap, results

    def get_minipools(self, limit=10):
        result = {
            "half" : self.get_minipools_by_type("minipools.available.half", limit),
            "full" : self.get_minipools_by_type("minipools.available.full", limit),
            "empty": self.get_minipools_by_type("minipools.available.empty", limit)
        }
        return result

    def get_dai_eth_price(self):
        data = self.call("DAIETH_univ3.slot0")
        value_dai = data[0] ** 2 / 2 ** 192
        return 1 / value_dai

    def get_minipool_count_per_status(self):
        offset, limit = 0, 10000
        minipool_count_per_status = [0, 0, 0, 0, 0]
        while True:
            log.debug(f"getMinipoolCountPerStatus({offset}, {limit})")
            tmp = self.call("rocketMinipoolManager.getMinipoolCountPerStatus", offset, limit)
            for i in range(len(tmp)):
                minipool_count_per_status[i] += tmp[i]
            if sum(tmp) < limit:
                break
            offset += limit
        return minipool_count_per_status


rp = RocketPool()
