from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
import random

from utils import solidity
from utils.rocketpool import rp

cached_image_hash = None
cached_image = None


def get_graph(current_commission, current_node_demand):
    global cached_image_hash
    global cached_image

    # get values from contracts
    min_fee = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getMinimumNodeFee"), decimals=16)
    max_fee = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getMaximumNodeFee"), decimals=16)
    if min_fee == max_fee:
        return None
    target_fee = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getTargetNodeFee"), decimals=16)
    demand_range = solidity.to_float(rp.call("rocketDAOProtocolSettingsNetwork.getNodeFeeDemandRange"))

    if cached_image_hash == [min_fee, target_fee, max_fee, demand_range, current_node_demand]:
        cached_image.seek(0)
        return cached_image
    elif cached_image and not cached_image.closed:
        cached_image.close()

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
    box_start = None
    if current_node_demand <= left_border:
        color = "red"
        box_start = left_side
    elif current_node_demand >= right_border:
        color = "green"
        box_start = right_border
    if box_start:
        c = int(current_commission) if int(current_commission) == current_commission else round(current_commission, 2)
        ax.add_patch(plt.Rectangle((box_start, min_fee - 1),
                                   right_side - right_border,
                                   max_fee - min_fee + 2,
                                   fill=False,
                                   hatch='...',
                                   color=color))
    if box_start < 0:
        # TODO load from file at module import
        tmp = [
            [20, "Maybe go outside. Its gonna take a while..."],
            [20, "Yep. Still 5%"],
            [20, "What did you expect?"],
            [20, "How longer must we suffer?"],
            [20, "Remember when commissions were 15%?"],
            [20, "And they don't stop coming"],
            [10, "Hope you have 32ETH ready c:"],
            [10, "Anybody want some cheap rETH?"],
            [10, ":catJAM:"],
            [10, "much stale eth. such queue"],
            [10, "We need people depositing rETH... wait no!"],
            [10, "o_O"],
            [10, "¯\\_(ツ)_/¯"],
            [10, "pls low gas wen"],
            [5, "shouldve deployed on cardano"],
            [5, "shouldve deployed on solana"],
            [5, "woooo yeah 5% GO GO GO"],
            [5, "too bullish"],
            [5, "too bearish"],
            [5, "Anybody seen the dot recently?"],
            [5, "What if the queue never depletes?"],
            [1, "RPL to 666$"],
            [1, "RPL to 0.69 ETH"],
            [1, "shouldve deployed on bitcoin"],
            [1, "Negative Commissions wen?"],
            [0.1, "dont tell anyone but im running out of ideas"],
            [0.1, "This is a secret message. Or is it?"],
            [0.01, "pog"]
        ]
        weights, strings = list(zip(*tmp))
        text = random.choices(strings, weights=weights)
        ax.text(0, max_fee - 2, text[0],
                fontsize=12, color="black", ha='center', va='center', weight='italic')

    # current commission dot
    ax.plot(current_node_demand, current_commission, 'o', color='black')

    # store the graph in an file object
    figfile = BytesIO()
    fig.savefig(figfile, format='png')
    figfile.seek(0)
    
    # clear plot from memory
    fig.clf()
    plt.close()

    # store image in cache
    cached_image_hash = [min_fee, target_fee, max_fee, demand_range, current_node_demand]
    cached_image = figfile
    return figfile
