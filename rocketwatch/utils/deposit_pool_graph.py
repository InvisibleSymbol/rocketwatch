from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
import random

from utils import solidity
from utils.rocketpool import rp


def get_graph(file, current_commission, current_node_demand):

    # get values from contracts
    min_fee = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getMinimumNodeFee"), decimals=16)
    max_fee = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getMaximumNodeFee"), decimals=16)
    if min_fee == max_fee:
        return None
    target_fee = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getTargetNodeFee"), decimals=16)
    demand_range = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getNodeFeeDemandRange"))

    # define vertical lines
    left_border = -demand_range
    left_side = left_border * 1.2
    right_border = demand_range
    right_side = right_border * 1.2

    # left part of the graph
    x_left = np.linspace(left_border, 0, 20)
    left_multiplier = target_fee - min_fee
    func_left = left_multiplier * ((x_left / demand_range) ** 3) + target_fee

    # extend the graph to the left
    x_extended_left = np.linspace(left_side, left_border, 2)
    func_extended_left = x_extended_left / x_extended_left * min_fee

    # right part of the graph
    x_right = np.linspace(0, right_border, 20)
    right_multiplier = max_fee - target_fee
    func_right = right_multiplier * ((x_right / demand_range) ** 3) + target_fee

    # extend the graph to the right
    x_extended_right = np.linspace(right_border, right_side, 2)
    func_extended_right = x_extended_right / x_extended_right * max_fee

    # combine all parts
    x = np.concatenate((x_extended_left, x_left, x_right, x_extended_right))
    func = np.concatenate((func_extended_left, func_left, func_right, func_extended_right))

    # prepare the graph
    fig, ax = plt.subplots()
    ax.set_xlim(left_side, right_side)
    ax.set_ylim(min_fee - 1, max_fee + 1)
    ax.grid(True)

    # labels
    ax.set_xlabel("Node Demand (ETH)")
    ax.set_ylabel("Commission Fee (%)")

    # draw the function
    ax.plot(x, func, color='blue')

    # vertical indicators
    ax.axvline(x=current_node_demand, color='black')
    ax.axvline(x=left_border, color='red')
    ax.axvline(x=right_border, color='green')

    # show current percentage boldly in the middle
    # add out-of-range rectangles
    box_start = 0
    if current_node_demand <= left_border:
        color = "red"
        box_start = left_side
    elif current_node_demand >= right_border:
        color = "green"
        box_start = right_border
    if box_start:
        ax.add_patch(plt.Rectangle((box_start, min_fee - 1),
                                   right_side - right_border,
                                   max_fee - min_fee + 2,
                                   fill=False,
                                   hatch='///',
                                   color=color))
    if box_start < 0:
        # TODO load from file at module import
        strings = [
            "Maybe go outside. Its gonna take a while...",
            "Yep. Still 5%",
            "What did you expect?",
            "How much longer must we suffer?",
            "Remember when commissions were 15%?",
            "And they don't stop coming",
            "Hope you have 32ETH ready c:",
            "Anybody want some cheap rETH?",
            ":catJAM:",
            "much stale eth. such queue",
            "We need people depositing rETH... wait no!",
            "o_O",
            "¯\\_(°-°)_/¯",
            "pls low gas wen",
            "shouldve deployed on cardano",
            "shouldve deployed on solana",
            "woooo yeah 5% GO GO GO",
            "too bullish",
            "too bearish",
            "Anybody seen the dot recently?",
            "What if the queue never depletes?",
            "RPL to 666$",
            "RPL to 0.69 ETH",
            "shouldve deployed on bitcoin",
            "Negative Commissions wen?",
            "dont tell anyone but im running out of ideas",
            "This is a secret message. Or is it?",
            "pog",
        ]
        text = random.choices(strings)
        ax.text(0, max_fee - 2, text[0],
                fontsize=12, color="black", ha='center', va='center')

    # current commission dot
    ax.plot(current_node_demand, current_commission, 'o', color='black')

    # store the graph in an file object
    fig.savefig(file, format='png')
    file.seek(0)
    
    # clear plot from memory
    fig.clf()
    plt.close()

    # store image in cache
    return True
