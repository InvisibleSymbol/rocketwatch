import datetime
import logging

import requests

from utils import solidity
from utils.cfg import cfg
from utils.rocketpool import rp

log = logging.getLogger("Rewards")
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


def get_minipool_count_per_node_histogram():
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

    # create a histogram by minipool count
    histogram = {}
    for node_id, mp_count in all_nodes.items():
        if mp_count in histogram:
            histogram[mp_count] += 1
        else:
            histogram[mp_count] = 1

    return [
        (mp_count, histogram[mp_count])
        for mp_count in sorted(histogram, reverse=True)
    ]


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
        "time": int(entry["blockTime"])
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
    eligible_nodes = {node["id"]:node["effectiveRPLStaked"] for node in data["nodes"]}

    # remove nodes that have already claimed rewards
    for claim in data["rplrewardIntervals"][0]["rplRewardClaims"]:
        if claim["claimer"] in eligible_nodes:
            eligible_nodes.pop(claim["claimer"])

    total_rewards = solidity.to_float(data["rplrewardIntervals"][0]["claimableNodeRewards"])
    claimed_rewards = solidity.to_float(data["rplrewardIntervals"][0]["totalNodeRewardsClaimed"])
    total_rpl_staked = solidity.to_float(rp.call("rocketNetworkPrices.getEffectiveRPLStake"))

    # get theoretical rewards per staked RPL
    reward_per_staked_rpl = total_rewards / total_rpl_staked

    # calculate Rewards required for eligible nodes
    rewards_required = reward_per_staked_rpl * sum(
        solidity.to_float(v) for v in eligible_nodes.values()
    )
    potential_rollover = (total_rewards - claimed_rewards) - rewards_required

    return rewards_required, potential_rollover


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

    # calculate potential rollover
    potential_rollover = (total_rewards - claimed_rewards) - rewards_required

    return rewards_required, potential_rollover
