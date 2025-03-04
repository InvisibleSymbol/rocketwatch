import logging
from typing import Optional
from cachetools.func import ttl_cache

from ens import ENS
from eth_typing import ChecksumAddress

from utils.cfg import cfg
from utils.shared_w3 import mainnet_w3

log = logging.getLogger("cached_ens")
log.setLevel(cfg["log_level"])


class CachedEns:
    def __init__(self):
        self.ens = ENS.from_web3(mainnet_w3)

    @ttl_cache(ttl=300)
    def get_name(self, address: ChecksumAddress) -> Optional[str]:
        log.debug(f"Retrieving ENS name for {address}")
        return self.ens.name(address)

    @ttl_cache(ttl=300)
    def resolve_name(self, name: str) -> Optional[ChecksumAddress]:
        log.debug(f"Resolving ENS name {name}")
        return self.ens.address(name)
