from web3 import Web3, HTTPProvider

from utils.cfg import cfg
from web3.beacon import Beacon as Bacon

chain = cfg['rocketpool.chain']

w3 = Web3(HTTPProvider(f"https://eth-{chain}.alchemyapi.io/v2/{cfg['rocketpool.alchemy_secret']}"))
mainnet_w3 = w3
bacon = Bacon(f"https://{cfg['rocketpool.infura_beacon_id']}:{cfg['rocketpool.infura_beacon_secret']}@eth2-beacon-mainnet.infura.io")

if chain != "mainnet":
    mainnet_w3 = Web3(HTTPProvider(f"https://eth-mainnet.alchemyapi.io/v2/{cfg['rocketpool.mainnet_alchemy_secret']}"))

    # required for block parsing on PoA networks like goerli
    # https://web3py.readthedocs.io/en/stable/middleware.html#geth-style-proof-of-authority
    from web3.middleware import geth_poa_middleware

    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
