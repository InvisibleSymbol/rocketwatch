import logging

import requests

from utils import solidity
from utils.cfg import cfg

log = logging.getLogger("thegraph")
log.setLevel(cfg["log_level"])


def get_uniswap_pool_stats(pool_address):
    query = """
    query pool($poolAddress: String!) {
        pool(id: $poolAddress) {
            tick
            sqrtPrice
        }
    }
    """
    # do the request
    response = requests.post(
        "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
        json={'query': query, 'variables': {'poolAddress': pool_address}}
    )

    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])

    # get the data
    data = response.json()["data"]

    return data["pool"]


def get_uniswap_pool_depth(pool_address):
    # get the pool stats
    pool_stats = get_uniswap_pool_stats(pool_address)

    # get the tick
    tick = int(pool_stats["tick"])

    # get the surrounding ticks
    query = """
    query surroundingTicks($poolAddress: String!, $tickIdxLowerBound: BigInt!, $tickIdxUpperBound: BigInt!, $skip: Int!) {
        ticks(
            subgraphError: allow
            first: 1000
            skip: $skip
            where: {poolAddress: $poolAddress, tickIdx_lte: $tickIdxUpperBound, tickIdx_gte: $tickIdxLowerBound}
        ) {
            tickIdx
            liquidityGross
            liquidityNet
            price0
            price1
            __typename
        }
    }
    """
    # do the request
    response = requests.post(
        "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
        json={'query'    : query,
              'variables': {'poolAddress': pool_address, 'tickIdxLowerBound': tick - 12000, 'tickIdxUpperBound': tick + 12000,
                            'skip'       : 0}}
    )

    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])

    # get the data
    data = response.json()["data"]

    # convert to (price1, liquidity) tuples
    ticks = [(float(tick["price1"]), float(tick["liquidityNet"])) for tick in data["ticks"]]
    # order by price
    ticks.sort(key=lambda x: x[0], reverse=True)

    # cumulatively sum the liquidity
    for i in range(1, len(ticks)):
        ticks[i] = (ticks[i][0], ticks[i][1] + ticks[i - 1][1])

    ticks.sort(key=lambda x: x[0])

    for i in range(len(ticks)):
        ticks[i] = (ticks[i][0], solidity.to_float(ticks[i][1]))

    # offset every liquidity number so that the minimum is 0
    min_liquidity = min([tick[1] for tick in ticks])
    for i in range(len(ticks)):
        ticks[i] = (ticks[i][0], ticks[i][1] - min_liquidity)
    return ticks
