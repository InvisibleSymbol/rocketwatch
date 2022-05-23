from utils import solidity
from utils.rocketpool import rp
from utils.shared_w3 import w3

price_cache = {
    "block"     : 0,
    "rpl_price" : 0,
    "reth_price": 0
}

sea_creatures = {
    # 32 * 100: spouting whale emoji
    32 * 100: '🐳',
    # 32 * 50: whale emoji
    32 * 50 : '🐋',
    # 32 * 30: shark emoji
    32 * 30 : '🦈',
    # 32 * 20: dolphin emoji
    32 * 20 : '🐬',
    # 32 * 10: otter emoji
    32 * 10 : '🦦',
    # 32 * 5: octopus emoji
    32 * 5  : '🐙',
    # 32 * 2: fish emoji
    32 * 2  : '🐟',
    # 32 * 1: fried shrimp emoji
    32 * 1  : '🍤',
    # 5: snail emoji
    5       : '🐌',
    # 1: microbe emoji
    1       : '🦠'
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
    resp = rp.multicall.aggregate(
        rp.get_contract_by_name(name).functions.balanceOf(address) for name in
        ["rocketTokenRPL", "rocketTokenRPLFixedSupply", "rocketTokenRETH"]
    )
    # add their tokens to their eth balance
    for token in resp.results:
        contract_name = rp.get_name_by_address(token.contract_address)
        if "RPL" in contract_name:
            eth_balance += solidity.to_float(token.results[0]) * price_cache["rpl_price"]
        if "RETH" in contract_name:
            eth_balance += solidity.to_float(token.results[0]) * price_cache["reth_price"]
    # get minipool count
    minipools = rp.call("rocketMinipoolManager.getNodeMinipoolCount", address)
    eth_balance += minipools * 16
    # add their staked RPL
    staked_rpl = solidity.to_int(rp.call("rocketNodeStaking.getNodeRPLStake", address))
    eth_balance += staked_rpl * price_cache["rpl_price"]
    # return the sea creature for the given holdings
    return get_sea_creature_for_holdings(eth_balance)
