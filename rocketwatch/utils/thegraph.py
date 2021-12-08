import datetime
import logging

import requests

from utils import solidity
from utils.cfg import cfg

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
    nodes(first: {count}, skip: {offset}, orderBy: stakingMinipools, orderDirection: desc) {{
          id
          stakingMinipools
    }}
}}
    """
    # do partial request, 1000 nodes per page
    offset = 0
    count = 1000
    all_nodes = []
    while True:
        # do the request
        log.debug(f"Requesting {count} nodes from offset {offset}")
        response = requests.post(
            cfg["graph_endpoint"],
            json={'query': query.format(count=count, offset=offset)}
        )
        # parse the response
        if "errors" in response.json():
            raise Exception(response.json()["errors"])
        data = response.json()["data"]
        nodes = data["nodes"]
        if not nodes:
            break
        all_nodes.extend(nodes)
        if len(nodes) < count:
            break
        offset += 1000

    # create histogram of minipool count
    histogram = {}
    for node in all_nodes:
        count = node["stakingMinipools"]
        if count not in histogram:
            histogram[count] = 0
        histogram[count] += 1
    return histogram


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
