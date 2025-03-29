import logging
import os
from pathlib import Path
from typing import Optional

from bidict import bidict
from cachetools import cached, FIFOCache
from cachetools.func import ttl_cache
from multicall import Call, Multicall
from web3.exceptions import ContractLogicError
from web3_multicall import Multicall as Web3Multicall

from utils import solidity
from utils.cfg import cfg
from utils.readable import decode_abi
from utils.shared_w3 import w3, mainnet_w3, historical_w3
from utils.time_debug import timerun

log = logging.getLogger("rocketpool")
log.setLevel(cfg["log_level"])

# no address found exception
class NoAddressFound(Exception):
    pass

class RocketPool:
    ADDRESS_CACHE = FIFOCache(maxsize=2048)
    ABI_CACHE = FIFOCache(maxsize=2048)
    CONTRACT_CACHE = FIFOCache(maxsize=2048)

    def __init__(self):
        self.addresses = bidict()
        self.multicall = Web3Multicall(w3.eth)
        self.flush()

    def flush(self):
        log.warning("FLUSHING RP CACHE")
        self.CONTRACT_CACHE.clear()
        self.ABI_CACHE.clear()
        self.ADDRESS_CACHE.clear()
        self.addresses = bidict()
        self._init_contract_addresses()

    def _init_contract_addresses(self) -> None:
        manual_addresses = cfg["rocketpool.manual_addresses"]
        for name, address in manual_addresses.items():
            self.addresses[name] = address

        self.addresses["multicall3"] = self.multicall.address

        log.info("Indexing Rocket Pool contracts...")
        # generate list of all file names with the .sol extension from the rocketpool submodule
        for path in Path("contracts/rocketpool/contracts/contract").rglob('*.sol'):
            # append to list but ensure that the first character is lowercase
            file_name = path.stem
            contract = file_name[0].lower() + file_name[1:]
            try:
                self.get_address_by_name(contract)
            except Exception:
                log.warning(f"Skipping {contract} in function list generation")
                continue

        cs_dir, cs_prefix = "ConstellationDirectory", "Constellation"
        self.addresses |= {
            f"{cs_prefix}.SuperNodeAccount": self.call(f"{cs_dir}.getSuperNodeAddress"),
            f"{cs_prefix}.OperatorDistributor": self.call(f"{cs_dir}.getOperatorDistributorAddress"),
            f"{cs_prefix}.Whitelist": self.call(f"{cs_dir}.getWhitelistAddress"),
            f"{cs_prefix}.ETHVault": self.call(f"{cs_dir}.getWETHVaultAddress"),
            f"{cs_prefix}.RPLVault": self.call(f"{cs_dir}.getRPLVaultAddress"),
            "WETH": self.call(f"{cs_dir}.getWETHAddress")
        }

    @staticmethod
    def seth_sig(abi, function_name):
        # also handle tuple outputs, so `example(unit256)((unit256,unit256))` for example
        for item in abi:
            if item.get("name") == function_name:
                inputs = ','.join([i['type'] for i in item['inputs']])
                outputs = []
                for o in item['outputs']:
                    if o['type'] == 'tuple':
                        outputs.append(f"({','.join([i['type'] for i in o['components']])})")
                    else:
                        outputs.append(o['type'])
                outputs = ','.join(outputs)
                return f"{function_name}({inputs})({outputs})"
        raise Exception(f"Function {function_name} not found in ABI")

    @timerun
    def multicall2_do_call(self, calls: list[Call], require_success=True):
        multicall = Multicall(calls, _w3=w3, gas_limit=500_000_000, require_success=require_success)
        return multicall()

    @cached(cache=ADDRESS_CACHE)
    def get_address_by_name(self, name):
        # manual overwrite at init
        if name in self.addresses:
            return self.addresses[name]
        return self.uncached_get_address_by_name(name)

    def uncached_get_address_by_name(self, name, block="latest"):
        log.debug(f"Retrieving address for {name} Contract")
        sha3 = w3.soliditySha3(["string", "string"], ["contract.address", name])
        address = self.get_contract_by_name("rocketStorage", historical=block != "latest").functions.getAddress(sha3).call(block_identifier=block)
        if not w3.toInt(hexstr=address):
            raise NoAddressFound(f"No address found for {name} Contract")
        self.addresses[name] = address
        log.debug(f"Retrieved address for {name} Contract: {address}")
        return address

    @staticmethod
    def get_revert_reason(tnx):
        try:
            w3.eth.call(
                {
                    "from"    : tnx["from"],
                    "to"      : tnx["to"],
                    "data"    : tnx["input"],
                    "gas"     : tnx["gas"],
                    "gasPrice": tnx["gasPrice"],
                    "value"   : tnx["value"]
                },
                block_identifier=tnx.blockNumber
            )
        except ContractLogicError as err:
            log.debug(f"Transaction: {tnx.hash} ContractLogicError: {err}")
            return ", ".join(err.args)
        except ValueError as err:
            log.debug(f"Transaction: {tnx.hash} ValueError: {err}")
            match err.args[0]["code"]:
                case -32000:
                    return "Out of gas"
                case _:
                    return "Hidden Error"
        else:
            return None

    @cached(cache=ABI_CACHE)
    def get_abi_by_name(self, name):
        return self.uncached_get_abi_by_name(name)

    def uncached_get_abi_by_name(self, name):
        log.debug(f"Retrieving abi for {name} Contract")
        sha3 = w3.soliditySha3(["string", "string"], ["contract.abi", name])
        compressed_string = self.get_contract_by_name("rocketStorage").functions.getString(sha3).call()
        if not compressed_string:
            raise Exception(f"No abi found for {name} Contract")
        return decode_abi(compressed_string)

    @cached(cache=CONTRACT_CACHE)
    def assemble_contract(self, name, address=None, historical=False, mainnet=False):
        if name.startswith("Constellation."):
            short_name = name.removeprefix("Constellation.")
            abi_path = f"./contracts/constellation/{short_name}.abi.json"
        else:
            abi_path = f"./contracts/{name}.abi.json"

        if os.path.exists(abi_path):
            with open(abi_path, "r") as f:
                abi = f.read()
        else:
            abi = self.get_abi_by_name(name)

        if mainnet:
            return mainnet_w3.eth.contract(address=address, abi=abi)
        if historical:
            return historical_w3.eth.contract(address=address, abi=abi)
        return w3.eth.contract(address=address, abi=abi)

    def get_name_by_address(self, address):
        return self.addresses.inverse.get(address, None)

    def get_contract_by_name(self, name, historical=False, mainnet=False):
        address = self.get_address_by_name(name)
        return self.assemble_contract(name, address, historical=historical, mainnet=mainnet)

    def get_contract_by_address(self, address):
        """
        **WARNING**: only call after contract has been previously retrieved using its name
        """
        name = self.get_name_by_address(address)
        return self.assemble_contract(name, address)

    def estimate_gas_for_call(self, path, *args, block="latest"):
        log.debug(f"Estimating gas for {path} (block={block})")
        name, function = path.rsplit(".", 1)
        contract = self.get_contract_by_name(name)
        return contract.functions[function](*args).estimateGas({"gas": 2 ** 32},
                                                               block_identifier=block)

    def get_function(self, path, *args, historical=False, address=None, mainnet=False):
        name, function = path.rsplit(".", 1)
        if not address:
            address = self.get_address_by_name(name)
        contract = self.assemble_contract(name, address, historical, mainnet)
        return contract.functions[function](*args)

    def call(self, path, *args, block="latest", address=None, mainnet=False):
        log.debug(f"Calling {path} (block={block})")
        return self.get_function(path, *args, historical=block != "latest", address=address, mainnet=mainnet).call(block_identifier=block)

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

    @ttl_cache(ttl=60)
    def get_eth_usdc_price(self) -> float:
        from utils.liquidity import UniswapV3
        pool_address = self.get_address_by_name("UniV3_USDC_ETH")
        return 1 / UniswapV3.Pool(pool_address).get_normalized_price()

    @ttl_cache(ttl=60)
    def get_reth_eth_price(self) -> Optional[float]:
        from utils.liquidity import UniswapV3
        pool_address = self.get_address_by_name("UniV3_rETH_ETH")
        return UniswapV3.Pool(pool_address).get_normalized_price()


rp = RocketPool()
