import datetime
import logging

import requests

from utils import solidity
from utils.cfg import cfg
from utils.rocketpool import rp

log = logging.getLogger("thegraph")
log.setLevel(cfg["log_level"])


def get_minipool_counts_per_node():
    query = """
    {{
        nodes(first: {count}, skip: {offset}, orderBy: id, orderDirection: desc) {{
            minipools(first: {count}, skip: {mp_offset}, orderBy: id) {{
                id
            }}
            id
        }}
    }}
    """
    # do partial request, 1000 nodes per page
    node_offset = 0
    minipool_offset = 0
    count = 1000
    all_nodes = {}
    # goal: we want to get the total minipool count per node
    # get the first batch
    # if there is a node with 1000 minipools, we do another request with an increased minipool offset, but the same node offset
    # if there is no node with 1000 minipools, but we have 1000 nodes, we do another request with an increased node offset, but a minimum minipool offset of 0
    # if there are less than 1000 nodes, we process them and then stop
    while True:
        log.debug(f"Requesting nodes with offset {node_offset} and minipools with offset {minipool_offset}")
        # do the request
        response = requests.post(
            cfg["graph_endpoint"],
            json={'query': query.format(count=count, offset=node_offset, mp_offset=minipool_offset)}
        )
        # parse the response
        if "errors" in response.json():
            raise Exception(response.json()["errors"])
        # get the data
        data = response.json()["data"]["nodes"]
        # process the data
        for node in data:
            # get the minipools
            mp_count = len(node["minipools"])
            # add the minipools to the node
            if node["id"] in all_nodes:
                all_nodes[node["id"]] += mp_count
            else:
                all_nodes[node["id"]] = mp_count
        # if there was a node with 1000 minipools, we do another request with an increased minipool offset, but the same node offset
        if max(len(node["minipools"]) for node in data) == count:
            minipool_offset += count
        elif len(data) == count:
            node_offset += count
            minipool_offset = 0
        else:
            break

    # return an array where each element represents a single node, and the value stored is the minipool count
    return sorted([mp_count for _, mp_count in all_nodes.items()])


def get_node_minipools_and_collateral():
    # get node addresses
    nodes = rp.call("rocketNodeManager.getNodeAddresses", 0, 10_000)
    node_staking = rp.get_contract_by_name("rocketNodeStaking")
    # get their RPL stake using rocketNodeStaking.getNodeRPLStake
    rpl_stakes = rp.multicall.aggregate(
        [node_staking.functions.getNodeRPLStake(node) for node in nodes]
    )
    rpl_stakes = [r.results[0] for r in rpl_stakes.results]
    # get the minipool sizes using rocketMinipoolManager.getNodeStakingMinipoolCountBySize
    minipool_manager = rp.get_contract_by_name("rocketMinipoolManager")
    eb16s = rp.multicall.aggregate(
        minipool_manager.functions.getNodeStakingMinipoolCountBySize(node, 16 * 10**18) for node in nodes
    )
    eb16s = [r.results[0] for r in eb16s.results]
    eb8s = rp.multicall.aggregate(
        minipool_manager.functions.getNodeStakingMinipoolCountBySize(node, 8 * 10**18) for node in nodes
    )
    eb8s = [r.results[0] for r in eb8s.results]
    return {
        nodes[i]: {
            "eb8s"     : eb8s[i],
            "eb16s"    : eb16s[i],
            "rplStaked": rpl_stakes[i]
        } for i in range(len(nodes))
    }


def get_average_collateral_percentage_per_node(collateral_cap, bonded):
    # get stakes for each node
    stakes = list(get_node_minipools_and_collateral().values())
    # get the current rpl price
    rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))

    result = {}
    # process the data
    for node in stakes:
        # get the minipool eth value
        minipool_value = int(node["eb16s"]) * 16 + int(node["eb8s"]) * (8 if bonded else 24)
        if not minipool_value:
            continue
        # rpl stake value
        rpl_stake_value = solidity.to_float(node["rplStaked"]) * rpl_price
        # cap rpl stake at x% of minipool_value using collateral_cap
        if collateral_cap:
            rpl_stake_value = min(rpl_stake_value, minipool_value * collateral_cap / 100)
        # calculate percentage
        percentage = rpl_stake_value / minipool_value * 100
        # round percentage to 5% steps
        percentage = (percentage // 5) * 5
        # add to result
        if percentage not in result:
            result[percentage] = []
        result[percentage].append(rpl_stake_value / rpl_price)

    return result


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
