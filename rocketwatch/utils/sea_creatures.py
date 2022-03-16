from utils import solidity
from utils.rocketpool import rp
from utils.shared_w3 import w3

price_cache = {
    "block": 0,
    "rpl_price": 0,
    "reth_price": 0
}

sea_creatures = {
    # 32 * 60: spouting whale emoji
    32 * 60: '🐳',
    # 32 * 30: whale emoji
    32 * 30: '🐋',
    # 32 * 15: shark emoji
    32 * 15: '🦈',
    # 32 * 10: dolphin emoji
    32 * 10: '🐬',
    # 32 * 5: octopus emoji
    32 * 5 : '🐙',
    # 32 * 2: fish emoji
    32 * 2 : '🐟',
    # 32 * 1: fired shrimp emoji
    32 * 1 : '🍤',
}


def get_sea_creature_for_holdings(holdings):
    """
    Returns the sea creature for the given holdings.
    :param holdings: The holdings to get the sea creature for.
    :return: The sea creature for the given holdings.
    """
    # if the holdings are more than 2 times the highest sea creature, return the highest sea creature with a multiplier next to it
    highest_possible_holdings = max(sea_creatures.keys())
    if holdings >= 2 * highest_possible_holdings:
        return sea_creatures[highest_possible_holdings] * int(holdings / highest_possible_holdings)
    for holding_value, sea_creature in sea_creatures.items():
        if holdings >= holding_value:
            return sea_creature
    return ''


def get_sea_creature_for_address(address):
    if price_cache["block"] == w3.eth.blockNumber:
        price_cache["rpl_price"] = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        price_cache["reth_price"] = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate"))

    # get their eth balance
    eth_balance = solidity.to_float(w3.eth.getBalance(address))
    # get ERC-20 token balance for this address
    tokens = w3.provider.make_request("alchemy_getTokenBalances",
                                      [address,
                                       [
                                           rp.get_address_by_name("rocketTokenRPL"),
                                           rp.get_address_by_name("rocketTokenRPLFixedSupply"),
                                           rp.get_address_by_name("rocketTokenRETH")],
                                       ])["result"]["tokenBalances"]
    # add their tokens to their eth balance
    for token in tokens:
        contract_name = rp.get_name_by_address(token["contractAddress"])
        if token["error"]:
            continue
        if "RPL" in contract_name:
            eth_balance += solidity.to_float(w3.toInt(hexstr=token["tokenBalance"])) * price_cache["rpl_price"]
        if "RETH" in contract_name:
            eth_balance += solidity.to_float(w3.toInt(hexstr=token["tokenBalance"])) * price_cache["reth_price"]
    # get minipool count
    minipools = rp.call("rocketMinipoolManager.getNodeMinipoolCount", address)
    eth_balance += minipools * 16
    # add their staked RPL
    staked_rpl = solidity.to_int(rp.call("rocketNodeStaking.getNodeRPLStake", address))
    eth_balance += staked_rpl * price_cache["rpl_price"]
    # return the sea creature for the given holdings
    return get_sea_creature_for_holdings(eth_balance)
