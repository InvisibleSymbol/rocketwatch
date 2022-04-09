from web3 import Web3, HTTPProvider

from utils.cfg import cfg
from web3.beacon import Beacon as Bacon

w3 = Web3(HTTPProvider(cfg['rocketpool.execution_layer.endpoint.current']))
mainnet_w3 = w3

if cfg['rocketpool.chain'] != "mainnet":
    mainnet_w3 = Web3(HTTPProvider(cfg['rocketpool.execution_layer.endpoint.mainnet']))

    # required for block parsing on PoA networks like goerli
    # https://web3py.readthedocs.io/en/stable/middleware.html#geth-style-proof-of-authority
    from web3.middleware import geth_poa_middleware

    w3.middleware_onion.inject(geth_poa_middleware, layer=0)

bacon = Bacon(cfg["rocketpool.consensus_layer.endpoint.current"])
mainnet_bacon = Bacon(cfg["rocketpool.consensus_layer.endpoint.mainnet"])
for b in [bacon, mainnet_bacon]:
    b.get_block = lambda block: bacon._make_get_request(f"/eth/v2/beacon/blocks/{block}")
    b.debug = lambda: bacon._make_get_request(f"/eth/v2/debug/beacon/states/head")
