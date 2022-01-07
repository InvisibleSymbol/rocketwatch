import logging
import requests
import time

from utils.cfg import cfg

PAGINATION = str(25)  # Number of transactions to fetch per request
TX_BY_ADDR_URL = "https://api.etherscan.io/api?module=account&action=txlist&sort=desc"

log = logging.getLogger("etherscan")
log.setLevel(cfg["log_level"])


def get_recent_account_transactions(address, block_count=44800):

    highest_block = 0
    page = 1
    lowest_block = float("inf")

    retries = 0

    txs = {}
    while retries < 3 and (highest_block == 0 or highest_block - lowest_block < block_count):
        options = {"address": address, "page": page,
                   "apikey": cfg["rocketpool.etherscan_secret"], "offset": PAGINATION}
        if highest_block > 0:
            options["endblock"] = str(highest_block)
        resp = requests.get(TX_BY_ADDR_URL, params=options)

        if not resp.status_code == 200:
            log.debug(
                f"Error querying etherscan, unexpected HTTP {str(resp.status_code)}")
            retries += 1
            continue

        parsed = resp.json()
        if "message" not in parsed or not parsed["message"].lower() == "ok":
            log.debug(resp.url, parsed["message"], parsed["result"])
            retries += 1
            continue

        for result in parsed["result"]:
            highest_block = max(highest_block, int(result["blockNumber"]))
            lowest_block = min(lowest_block, int(result["blockNumber"]))
            # Skip any transactions that aren't sending eth to the contract, because idk what that could mean
            if not result["to"] == address.lower():
                continue

            txs[result["hash"]] = result
        page += 1
    return txs if len(txs.keys()) > 0 else None
