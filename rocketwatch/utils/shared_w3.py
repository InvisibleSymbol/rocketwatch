import logging
import math

import circuitbreaker
import requests
from requests import HTTPError, ConnectTimeout
from retry import retry
from web3 import Web3, HTTPProvider
from web3.beacon import Beacon as Bacon
from web3.middleware import geth_poa_middleware

from utils.cfg import cfg

log = logging.getLogger("shared_w3")
log.setLevel(cfg["log_level"])

w3 = Web3(HTTPProvider(cfg['rocketpool.execution_layer.endpoint.current']))
mainnet_w3 = w3

if cfg['rocketpool.chain'] != "mainnet":
    mainnet_w3 = Web3(HTTPProvider(cfg['rocketpool.execution_layer.endpoint.mainnet']))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)

historical_w3 = None
if "historical" in cfg['rocketpool.execution_layer.endpoint'].keys():
    historical_w3 = Web3(HTTPProvider(cfg['rocketpool.execution_layer.endpoint.historical']))

endpoints = cfg["rocketpool.consensus_layer.endpoints"]
tmp = []
exceptions = (
    HTTPError, ConnectionError, ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout)
for fallback_endpoint in reversed(endpoints):
    class SuperBacon(Bacon):
        def __init__(
                self,
                base_url: str,
                session: requests.Session = requests.Session(),
        ) -> None:
            super().__init__(base_url, session)

        @retry(tries=3 if tmp else 1, exceptions=exceptions, delay=0.5)
        @retry(tries=5 if tmp else 1, exceptions=ValueError, delay=0.1)
        @circuitbreaker.circuit(failure_threshold=2 if tmp else math.inf,
                                recovery_timeout=15,
                                expected_exception=exceptions,
                                fallback_function=tmp[-1].get_block if tmp else None,
                                name=f"get_block using {fallback_endpoint}")
        def get_block(self, *args):
            block_id = args[-1]
            if len(args) > 1:
                log.warning(f"falling back to {self.base_url} for block {block_id}")
            endpoint = f"/eth/v2/beacon/blocks/{block_id}"
            url = self.base_url + endpoint
            response = self.session.get(url, timeout=(3.05, 20))
            if response.status_code == 404 and all(q in response.json()["message"].lower() for q in ["not", "found"]):
                raise ValueError("Block does not exist")
            response.raise_for_status()
            return response.json()

        @retry(tries=3 if tmp else 1, exceptions=exceptions, delay=0.5)
        @circuitbreaker.circuit(failure_threshold=2 if tmp else math.inf,
                                recovery_timeout=90,
                                fallback_function=tmp[-1].get_validator_balances if tmp else None,
                                name=f"get_validator_balances using {fallback_endpoint}")
        def get_validator_balances(self, *args, **kwargs):
            state_id = args[-1]
            if len(args) > 1:
                log.warning(f"falling back to {self.base_url} for validator balances {state_id}")
            endpoint = f"/eth/v1/beacon/states/{state_id}/validator_balances"
            # id array if present, and is array of ints
            if "ids" in kwargs and all(isinstance(i, int) for i in kwargs['ids']):
                # turn to array of strings
                kwargs['ids'] = [str(i) for i in kwargs['ids']]
                endpoint += f"?id={','.join(kwargs['ids'])}"
            url = self.base_url + endpoint
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            return response.json()

        def get_validators(self, *args, **kwargs):
            state_id = args[-1]
            if len(args) > 1:
                log.warning(f"falling back to {self.base_url} for validator balances {state_id}")
            endpoint = f"/eth/v1/beacon/states/{state_id}/validators"
            # id array if present, and is array of ints
            if "ids" in kwargs and isinstance(kwargs["ids"], list):
                # turn to array of strings
                kwargs['ids'] = [str(i) for i in kwargs['ids']]
                endpoint += f"?id={','.join(kwargs['ids'])}"
            url = self.base_url + endpoint
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            return response.json()


    tmp.append(SuperBacon(fallback_endpoint))
bacon = tmp[-1]
