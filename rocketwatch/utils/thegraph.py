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


def get_reth_ratio_past_week():
    query = """
{{
    networkStakerBalanceCheckpoints(orderBy: blockTime, orderDirection: asc, where: {{blockTime_gte: "{timestamp}"}}) {{
        rETHExchangeRate
        blockTime
    }}
}}
    """
    # get timestamp 7 days ago
    timestamp = datetime.datetime.now() - datetime.timedelta(days=7)
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


def get_average_collateral_percentage_per_node(cap_collateral):
    query = """
{
    nodes(orderBy: id, where: {stakingMinipools_not: "0"}, first: 1000) {
        rplStaked
        stakingMinipools
    }
    networkNodeBalanceCheckpoints(first: 1, orderBy: block, orderDirection: desc) {
        rplPriceInETH
        block
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

    rpl_eth_price = solidity.to_float(data["networkNodeBalanceCheckpoints"][0]["rplPriceInETH"])

    result = {}
    for node in data["nodes"]:
        minipool_worth = int(node["stakingMinipools"]) * 16
        rpl_stake = solidity.to_float(node["rplStaked"])
        effective_staked = rpl_stake * rpl_eth_price
        # round to 5 % increments
        collateral_percentage = effective_staked / minipool_worth
        if collateral_percentage < 0.1:
            collateral_percentage = 0
        collateral_percentage = round(round(collateral_percentage * 20) / 20 * 100, 0)
        if cap_collateral:
            collateral_percentage = min(collateral_percentage, 150)
        if collateral_percentage not in result:
            result[collateral_percentage] = []
        result[collateral_percentage].append(rpl_stake)

    return result

# use cols to pass columns you want to request as a list of strings
def scan_nodes(cols, count = 1000):
    node_query = """
{{
    nodes(first: {count}, skip: {offset}, orderBy: id, orderDirection: desc) {{
        {cols}
    }}
}}
    """

    # node request pagination
    page = 0
    data = []

    while True:

        response = requests.post(
            cfg["graph_endpoint"],
            json={'query': node_query.format(count = count, offset = page*count, cols = " ".join(cols))}
        )

        data.extend(response.json()["data"]["nodes"])

        # parse the response
        if "errors" in response.json():
            raise Exception(response.json()["errors"])

        # check if final page
        if len(response.json()["data"]["nodes"])<1000:
            break

        page = page + 1

    return data

def get_RPL_ETH_price():

    price_query = """
{
    networkNodeBalanceCheckpoints(first: 1, orderBy: block, orderDirection: desc) {
        rplPriceInETH
        block
    }
}
    """

    # do the request
    price_response = requests.post(
        cfg["graph_endpoint"],
        json={'query': price_query}
    )

    # parse the response
    if "errors" in price_response.json():
        raise Exception(price_response.json()["errors"])

    #retrieve price from data
    rpl_eth_price = solidity.to_float(price_response.json()["data"]["networkNodeBalanceCheckpoints"][0]["rplPriceInETH"])

    return rpl_eth_price

