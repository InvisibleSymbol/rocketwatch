import datetime
import logging

import requests

from utils import solidity
from utils.cfg import cfg
from utils.rocketpool import rp

log = logging.getLogger("thegraph")
log.setLevel(cfg["log_level"])


def get_average_commission():
    query = """
{
    rocketPoolProtocols(first: 1) {
        lastNetworkNodeBalanceCheckPoint {
            averageFeeForActiveMinipools
        }
    }
}
    """
    # do the request
    response = requests.post(
        cfg["graph_endpoint"],
        json={'query': query}
    )
    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])
    data = response.json()["data"]
    raw_value = int(data["rocketPoolProtocols"][0]["lastNetworkNodeBalanceCheckPoint"]["averageFeeForActiveMinipools"])
    return solidity.to_float(raw_value)


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


def get_reth_ratio_past_month():
    query = """
{{
    networkStakerBalanceCheckpoints(orderBy: blockTime, orderDirection: asc, where: {{blockTime_gte: "{timestamp}"}}) {{
        rETHExchangeRate
        blockTime
    }}
}}
    """
    # get timestamp 7 days ago
    timestamp = datetime.datetime.now() - datetime.timedelta(days=30)
    # do the request
    response = requests.post(
        cfg["graph_endpoint"],
        json={'query': query.format(timestamp=int(timestamp.timestamp()))}
    )
    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])
    # convert all entries to ints
    data = response.json()["data"]["networkStakerBalanceCheckpoints"]
    data = [{
        "value": solidity.to_float(int(entry["rETHExchangeRate"])),
        "time" : int(entry["blockTime"])
    } for entry in data]
    return data


def get_unclaimed_rpl_reward_nodes():
    # TODO: Make work with over 1000 nodes
    query = """
{{
    nodes(first: 1000, where: {{blockTime_lte: "{timestamp}", effectiveRPLStaked_gt: "0"}}) {{
        id
        effectiveRPLStaked
    }}
    rplrewardIntervals(first: 1, orderBy: intervalStartTime, orderDirection: desc) {{
        totalNodeRewardsClaimed
        claimableNodeRewards
        rplRewardClaims(first: 1000, where: {{claimerType: Node}}) {{
            claimer
        }}
    }}
}}
    """
    # get reward period start
    reward_start = rp.call("rocketRewardsPool.getClaimIntervalTimeStart")
    # duration left
    reward_duration = rp.call("rocketRewardsPool.getClaimIntervalTime")
    reward_end = reward_start + reward_duration
    # get timestamp 28 days from the last possible claim date
    timestamp = reward_end - (solidity.days * 28)

    # do the request
    response = requests.post(
        cfg["graph_endpoint"],
        json={'query': query.format(timestamp=timestamp)}
    )
    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])

    # get the data
    data = response.json()["data"]
    # get the eligible nodes for this interval
    eligible_nodes = {node["id"]: node["effectiveRPLStaked"] for node in data["nodes"]}

    # remove nodes that have already claimed rewards
    for claim in data["rplrewardIntervals"][0]["rplRewardClaims"]:
        if claim["claimer"] in eligible_nodes:
            eligible_nodes.pop(claim["claimer"])

    total_rewards = solidity.to_float(data["rplrewardIntervals"][0]["claimableNodeRewards"])
    claimed_rewards = solidity.to_float(data["rplrewardIntervals"][0]["totalNodeRewardsClaimed"])
    total_rpl_staked = solidity.to_float(rp.call("rocketNetworkPrices.getEffectiveRPLStake"))

    # get theoretical rewards per staked RPL
    reward_per_staked_rpl = total_rewards / total_rpl_staked

    # get list of eligible claims and sort by largest first
    eligible_effective = sorted([solidity.to_float(v) for v in eligible_nodes.values()], reverse=True)

    # calculate Rewards required for eligible nodes
    rewards_required = reward_per_staked_rpl * sum(eligible_effective)

    eligible_claims = [v * reward_per_staked_rpl for v in eligible_effective]
    impossible_amount = 0
    current_available_amount = total_rewards - claimed_rewards
    # simulate claims starting from largest to smallest
    for claim in eligible_claims:
        # if the claim is impossible, skip it
        if claim > current_available_amount:
            impossible_amount += claim
            continue
        # if the claim is possible, decrease the available amount
        current_available_amount -= claim

    return rewards_required, impossible_amount, current_available_amount


def get_unclaimed_rpl_reward_odao():
    query = """
{{
    nodes(first: 1000, where: {{oracleNodeBlockTime_lte: "{timestamp}", oracleNodeBlockTime_gt: "0"}}) {{
        id
        effectiveRPLStaked
    }}
    rplrewardIntervals(first: 1, orderBy: intervalStartTime, orderDirection: desc) {{
        totalODAORewardsClaimed
        claimableODAORewards
        rplRewardClaims(first: 1000, where: {{claimerType: ODAO}}) {{
            claimer
        }}
    }}
}}
    """
    # get reward period start
    reward_start = rp.call("rocketRewardsPool.getClaimIntervalTimeStart")
    # duration left
    reward_duration = rp.call("rocketRewardsPool.getClaimIntervalTime")
    reward_end = reward_start + reward_duration
    # get timestamp 28 days from the last possible claim date
    timestamp = reward_end - (solidity.days * 28)

    # do the request
    response = requests.post(
        cfg["graph_endpoint"],
        json={'query': query.format(timestamp=timestamp)}
    )

    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])

    # get the data
    data = response.json()["data"]
    # get the eligible nodes for this interval
    eligible_nodes = [node["id"] for node in data["nodes"]]

    # remove nodes that have already claimed rewards
    for claim in data["rplrewardIntervals"][0]["rplRewardClaims"]:
        if claim["claimer"] in eligible_nodes:
            eligible_nodes.remove(claim["claimer"])

    # get total rewards
    total_rewards = solidity.to_float(data["rplrewardIntervals"][0]["claimableODAORewards"])
    claimed_rewards = solidity.to_float(data["rplrewardIntervals"][0]["totalODAORewardsClaimed"])
    total_odao_members = rp.call("rocketDAONodeTrusted.getMemberCount")

    # get theoretical rewards per member
    reward_per_member = total_rewards / total_odao_members

    # calculate Rewards required for eligible nodes
    rewards_required = reward_per_member * len(eligible_nodes)

    # get list of eligible claims and sort by largest first
    eligible_claims = [reward_per_member] * len(eligible_nodes)
    impossible_amount = 0
    current_available_amount = total_rewards - claimed_rewards
    # simulate claims starting from largest to smallest
    for claim in eligible_claims:
        # if the claim is impossible, skip it
        if claim > current_available_amount:
            impossible_amount += claim
            continue
        # if the claim is possible, decrease the available amount
        current_available_amount -= claim

    return rewards_required, impossible_amount, current_available_amount


def get_claims_current_period():
    query = """
{
    rplrewardIntervals(first: 1, orderBy: intervalStartTime, orderDirection: desc) {
        rplRewardClaims(first: 1000, orderBy: ethAmount, where: {claimerType: Node}) {
            amount
            claimer
            ethAmount
        }
    }
}
    """
    # do the request
    response = requests.post(
        cfg["graph_endpoint"],
        json={'query': query}
    )

    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])

    # get the data
    data = response.json()["data"]

    return data["rplrewardIntervals"][0]["rplRewardClaims"]


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
    stakes = [
        {
            "eb8s"     : eb8s[i],
            "eb16s"    : eb16s[i],
            "rplStaked": rpl_stakes[i],
            "node"     : nodes[i]
        } for i in range(len(nodes))
    ]
    return stakes


def get_average_collateral_percentage_per_node(collateral_cap, bonded):
    # get stakes for each node
    stakes = get_node_minipools_and_collateral()
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
        percentage = round(percentage / 5) * 5
        # add to result
        if percentage not in result:
            result[percentage] = []
        result[percentage].append(rpl_stake_value / rpl_price)

    return result


def get_active_snapshot_proposals():
    query = """
{
  proposals(
    first: 20,
    skip: 0,
    where: {
      space_in: ["rocketpool-dao.eth", ""],
      state: "active"
    },
    orderBy: "created",
    orderDirection: desc
  ) {
    id
    title
    choices
    state
    scores
    scores_total
    scores_updated
    end
    quorum
  }
}
"""
    # do the request
    response = requests.post(
        "https://hub.snapshot.org/graphql",
        json={'query': query}
    )

    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])

    # get the data
    data = response.json()["data"]

    return data["proposals"]


def get_votes_of_snapshot(snapshot_id):
    query = """
{{
  votes (
    first: 1000
    skip: 0
    where: {{
      proposal: "{snapshot_id}"
    }}
    orderBy: "created",
    orderDirection: desc
  ) {{
    id
    voter
    created
    vp
    choice
    reason
  }}
  proposal(
    id:"{snapshot_id}"
  ) {{
    choices
    title
  }}
}}

"""
    # do the request
    response = requests.post(
        "https://hub.snapshot.org/graphql",
        json={'query': query.format(snapshot_id=snapshot_id)}
    )

    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])

    # get the data
    data = response.json()["data"]

    return data["votes"], data["proposal"]


"""
fetch("https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3", {
  "body": "{\"operationName\":\"pool\",\"variables\":{\"poolAddress\":\"0xe42318ea3b998e8355a3da364eb9d48ec725eb45\"},\"query\":\"query pool($poolAddress: String!) {\\n  pool(id: $poolAddress) {\\n    tick\\n    token0 {\\n      symbol\\n      id\\n      decimals\\n      __typename\\n    }\\n    token1 {\\n      symbol\\n      id\\n      decimals\\n      __typename\\n    }\\n    feeTier\\n    sqrtPrice\\n    liquidity\\n    __typename\\n  }\\n}\\n\"}",
  "method": "POST",
});"""


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


"""
fetch("https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3", {
  "body": "{\"operationName\":\"surroundingTicks\",\"variables\":{\"poolAddress\":\"0xe42318ea3b998e8355a3da364eb9d48ec725eb45\",\"tickIdxLowerBound\":22560,\"tickIdxUpperBound\":46560,\"skip\":0},\"query\":\"query surroundingTicks($poolAddress: String!, $tickIdxLowerBound: BigInt!, $tickIdxUpperBound: BigInt!, $skip: Int!) {\\n  ticks(\\n    subgraphError: allow\\n    first: 1000\\n    skip: $skip\\n    where: {poolAddress: $poolAddress, tickIdx_lte: $tickIdxUpperBound, tickIdx_gte: $tickIdxLowerBound}\\n  ) {\\n    tickIdx\\n    liquidityGross\\n    liquidityNet\\n    price0\\n    price1\\n    __typename\\n  }\\n}\\n\"}",
  "method": "POST",
});
"""


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

