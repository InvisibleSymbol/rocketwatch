from web3 import Web3, HTTPProvider

from utils.cfg import cfg

chain = cfg['rocketpool.chain']

w3 = Web3(HTTPProvider(f"https://{cfg['rocketpool.chain']}.infura.io/v3/{cfg['rocketpool.infura_secret']}"))
mainnet_w3 = w3

if chain != "mainnet":
    mainnet_w3 = Web3(HTTPProvider(f"https://mainnet.infura.io/v3/{cfg['rocketpool.infura_secret']}"))

    # required for block parsing on PoA networks like goerli
    # https://web3py.readthedocs.io/en/stable/middleware.html#geth-style-proof-of-authority
    from web3.middleware import geth_poa_middleware

    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
