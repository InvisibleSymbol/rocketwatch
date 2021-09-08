import logging
import os

from cachetools.func import ttl_cache
from ens import ENS

log = logging.getLogger("rocketpool")
log.setLevel(os.getenv("LOG_LEVEL"))


class CachedEns:
  def __init__(self, w3):
    self.w3 = w3
    self.ens = ENS.fromWeb3(self.w3)

  @ttl_cache(ttl=300)
  def get_name(self, address):
    log.debug(f"retrieving ens name for {address}")
    return self.ens.name(address)
