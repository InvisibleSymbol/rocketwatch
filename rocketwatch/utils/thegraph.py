
import requests
from utils.cfg import cfg

from utils import solidity


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
        json={'query': query},
    )
    # parse the response
    if "errors" in response.json():
        raise Exception(response.json()["errors"])
    data = response.json()["data"]
    raw_value = int(data["rocketPoolProtocols"][0]["lastNetworkNodeBalanceCheckPoint"]["averageFeeForActiveMinipools"])
    return solidity.to_float(raw_value)
