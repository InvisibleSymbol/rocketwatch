import contextlib
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
    def creature_for_value(amount):
        return next(((k, v) for k, v in sea_creatures.items() if amount >= k), (0, ''))

    def creatures_for_value(amount):
        highest_possible_holdings = max(sea_creatures.keys())
        lowest_possible_holdings = min(sea_creatures.keys())
        if amount < lowest_possible_holdings:
            return
        # if the holdings are more than 2 times the highest sea creature, return the highest sea creature with a multiplier next to it
        if amount >= 2 * highest_possible_holdings:
            yield sea_creatures[highest_possible_holdings] * int(amount / highest_possible_holdings)
            amount %= highest_possible_holdings
        else:
            # otherwise yield just 1 creature
            value, creature = creature_for_value(amount)
            yield creature
            amount -= value
        # If there is no remainder, exit now
        if amount < lowest_possible_holdings:
            return
        yield '.'
        # yield 3 decimal places
        for i in range(0,3):
            value, creature = creature_for_value(amount)
            yield creature
            amount -= value
        return

    return ''.join(creatures_for_value(holdings))


def get_holding_for_address(address):
    if price_cache["block"] != (b := w3.eth.blockNumber):
        price_cache["rpl_price"] = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        price_cache["reth_price"] = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate"))
        price_cache["block"] = b

    # get their eth balance
    eth_balance = solidity.to_float(w3.eth.getBalance(address))
    # get ERC-20 token balance for this address
    with contextlib.suppress(Exception):
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
    return eth_balance


def get_sea_creature_for_address(address):
    # return the sea creature for the given holdings
    return get_sea_creature_for_holdings(get_holding_for_address(address))
