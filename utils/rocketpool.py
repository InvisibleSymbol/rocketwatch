import logging
import os
import warnings

from bidict import bidict
from cachetools.func import ttl_cache, lru_cache
from web3.datastructures import MutableAttributeDict as aDict

log = logging.getLogger("rocketpool")
log.setLevel(os.getenv("LOG_LEVEL"))

# noinspection PyTypeChecker
memoize = lru_cache(maxsize=None)


class RocketPool:
  def __init__(self, w3, storage_address, deposit_address):
    self.w3 = w3
    self.addresses = bidict()
    self.storage_contract = self.get_contract("rocketStorage", storage_address)
    self.deposit_contract = self.get_contract("beaconDepositContract", deposit_address)

  @memoize
  def get_address_by_name(self, name):
    log.debug(f"retrieving address for {name} Contract")
    sha3 = self.w3.soliditySha3(["string", "string"], ["contract.address", name])
    address = self.storage_contract.functions.getAddress(sha3).call()
    self.addresses[name] = address
    return address

  @memoize
  def get_contract(self, name, address):
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

  def get_pubkey_using_contract(self, address):
    contract = self.get_contract_by_name("rocketMinipoolManager")
    return contract.functions.getMinipoolPubkey(address).call().hex()

  def get_pubkey_using_transaction(self, receipt):
    # will throw some warnings about other events but those are safe to ignore since we don't need those anyways
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      processed_logs = self.deposit_contract.events.DepositEvent().processReceipt(receipt)

    # attempt to retrieve the pubkey
    if processed_logs:
      deposit_event = processed_logs[0]
      return "0x" + deposit_event.args.pubkey.hex()

  @ttl_cache(ttl=300)
  def get_dao_member_name(self, member_address):
    contract = self.get_contract_by_name("rocketDAONodeTrusted")
    return contract.functions.getMemberID(member_address).call()

  def get_proposal_info(self, event):
    contract = self.get_contract_by_address(event['address'])
    result = {
      "message": contract.functions.getMessage(event.args.proposalID).call(),
      "votesFor": contract.functions.getVotesFor(event.args.proposalID).call() // 10 ** 18,
      "votesAgainst": contract.functions.getVotesAgainst(event.args.proposalID).call() // 10 ** 18,
    }
    return aDict(result)

  @ttl_cache(ttl=300)
  def is_minipool(self, address):
    contract = self.get_contract_by_name("rocketMinipoolManager")
    return contract.functions.getMinipoolExists(address).call()
