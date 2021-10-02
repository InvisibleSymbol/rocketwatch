import logging
import os
import warnings

from bidict import bidict
from cachetools.func import ttl_cache, lru_cache
from web3.datastructures import MutableAttributeDict as aDict

from utils import solidity

log = logging.getLogger("rocketpool")
log.setLevel(os.getenv("LOG_LEVEL"))

# noinspection PyTypeChecker
memoize = lru_cache(maxsize=None)


class RocketPool:
  def __init__(self, w3, storage_address):
    self.w3 = w3
    self.addresses = bidict()
    self.storage_contract = self.get_contract("rocketStorage", storage_address)

  @memoize
  def get_address_by_name(self, name):
    log.debug(f"Retrieving address for {name} Contract")
    sha3 = self.w3.soliditySha3(["string", "string"], ["contract.address", name])
    address = self.storage_contract.functions.getAddress(sha3).call()
    self.addresses[name] = address
    return address

  @memoize
  def get_contract(self, name, address=None):
    with open(f"./contracts/{name}.abi", "r") as f:
      contract = self.w3.eth.contract(address=address, abi=f.read())
    return contract

  def get_name_by_address(self, address):
    return self.addresses.inverse.get(address, None)

  def get_contract_by_name(self, name):
    address = self.get_address_by_name(name)
    return self.get_contract(name, address)

  def get_contract_by_address(self, address):
    """
    **WARNING**: only call after contract has been previously retrieved using its name
    """
    name = self.get_name_by_address(address)
    return self.get_contract(name, address)

  def call(self, path, *args):
    name, function = path.split(".")
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
      return "0x" + deposit_event.args.pubkey.hex()

  def get_annual_rpl_inflation(self):
    inflation_per_interval = solidity.to_float(self.call("rocketTokenRPL.getInflationIntervalRate"))
    seconds_per_interval = self.call("rocketTokenRPL.getInflationIntervalTime")
    intervals_per_year = solidity.years / seconds_per_interval
    return (inflation_per_interval ** intervals_per_year) - 1

  def get_percentage_rpl_swapped(self):
    value = solidity.to_float(self.call("rocketTokenRPL.totalSwappedRPL"))
    percentage = (value / 18_000_000) * 100
    return round(percentage, 2)
