import logging

from cachetools.func import ttl_cache
from ens import ENS

from utils.cfg import cfg
from utils.shared_w3 import mainnet_w3

log = logging.getLogger("cached_ens")
log.setLevel(cfg["log_level"])


class CachedEns:
  def __init__(self):
    self.ens = ENS.fromWeb3(mainnet_w3)

  @ttl_cache(ttl=300)
  def get_name(self, address):
    log.debug(f"retrieving ens name for {address}")
    return self.ens.name(address)

  @ttl_cache(ttl=300)
  def resolve_name(self, name):
    log.debug(f"resolving ens name {name}")
    return self.ens.resolve(name)
