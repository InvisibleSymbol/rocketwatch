import logging

import humanize
from colorama import Style
from discord.app_commands import describe
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.readable import render_tree
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.visibility import is_hidden

log = logging.getLogger("tvl")
log.setLevel(cfg["log_level"])


def split_rewards_logic(balance, node_share, commission, force_base=False):
    d = {
        "base"   : {
            "reth": 0,
            "node": 0
        },
        "rewards": {
            "reth": 0,
            "node": 0
        }
    }
    node_balance = 32 * node_share
    reth_balance = 32 - node_balance
    if balance >= 8 or force_base:
        # reth base share
        d["base"]["reth"] = min(balance, reth_balance)
        balance -= d["base"]["reth"]
        # node base share
        d["base"]["node"] = min(balance, node_balance)
        balance -= d["base"]["node"]
    # rewards split logic
    if balance > 0:
        node_ownership_share = node_share + (1 - node_share) * commission
        d["rewards"]["node"] = balance * node_ownership_share
        d["rewards"]["reth"] = balance * (1 - node_ownership_share)
    return d


class TVL(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @hybrid_command()
    @describe(show_all="Also show entries with 0 value")
    async def tvl(self,
                  ctx: Context,
                  show_all: bool = False):
        """
        Show the total value locked in the Protocol.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        data = {
            "Total RPL Locked": {
                "Staked RPL"       : {
                    "Node Operators": {},  # accurate, live
                    "oDAO Bond"     : {},  # accurate, live
                },
                "Unclaimed Rewards": {
                    "Node Operators & oDAO": {},  # accurate, live
                    "pDAO"                 : {},  # accurate, live
                },
                "Slashed RPL"      : {},  # accurate, live
                "Unused Inflation" : {},  # accurate, live
            },
            "Total ETH Locked": {
                "Minipools Stake"       : {
                    "Queued Minipools"   : {},  # accurate, db
                    "Pending Minipools"  : {},  # accurate, db
                    "Dissolved Minipools": {
                        "Locked on Beacon Chain": {},  # accurate, db
                        "Contract Balance"      : {},  # accurate, db
                    },
                    "Staking Minipools"  : {
                        # beacon chain balances of staking minipools but ceil at 32 ETH and node share gets penalties first
                        "rETH Share": {"_val": 0},  # done, db
                        "Node Share": {"_val": 0},  # done, db
                    }
                },
                "rETH Collateral"       : {
                    "Deposit Pool"    : {},  # accurate, live
                    "Extra Collateral": {},  # accurate, live
                },
                "Undistributed Balances": {
                    "Smoothing Pool Balance"    : {
                        "rETH Share": {"_val": 0, "_is_estimate": True},  # missing
                        "Node Share": {"_val": 0, "_is_estimate": True},  # missing
                    },
                    "Node Distributor Contracts": {
                        "rETH Share": {"_val": 0},  # done, db
                        "Node Share": {"_val": 0},  # done, db
                    },
                    "Minipool Contract Balances": {  # important, only after minipool has gone to state "staking"
                        "rETH Share": {"_val": 0},  # done, db
                        "Node Share": {"_val": 0},  # done, db
                    },
                    "Beacon Chain Rewards"      : {  # anything over 32, split acording to node share
                        "rETH Share": {"_val": 0},  # done, db
                        "Node Share": {"_val": 0},  # done, db
                    },
                },
                "Unclaimed Rewards"     : {
                    "Smoothing Pool": {},  # accurate, live
                }
            },
        }
        # note: _value in each dict will store the final string that gets rendered in the render

        eth_price = rp.get_dai_eth_price()
        rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        rpl_address = rp.get_address_by_name("rocketTokenRPL")

        # Queued Minipools: initialisedCount of minipool_count_per_status * 1 ETH.
        # Minipools that are flagged as initialised have the following applied to them:
        # - They have 1 ETH staked on the beacon chain.
        # - They have not yet received 31 ETH from the Deposit Pool.
        tmp = await self.db.minipools_new.aggregate([
            {
                '$match': {
                    'status': 'initialised',
                    'vacant': False
                }
            }, {
                '$group': {
                    '_id'           : 'total',
                    'beacon_balance': {
                        '$sum': 1
                    }
                }
            }
        ]).to_list(1)
        if tmp:
            data["Total ETH Locked"]["Minipools Stake"]["Queued Minipools"]["_val"] = tmp[0]["beacon_balance"]

        # Pending Minipools: prelaunchCount of minipool_count_per_status * 32 ETH.
        # Minipools that are flagged as prelaunch have the following applied to them:
        #  - They have deposited 1 ETH to the Beacon Chain.
        #  - They have 31 ETH from the Deposit Pool in their contract waiting to be staked as well.
        #  - They are currently in the scrubbing process (should be 12 hours) or have not yet initiated the second phase.
        tmp = await self.db.minipools_new.aggregate([
            {
                '$match': {
                    'status': 'prelaunch',
                    'vacant': False
                }
            }, {
                '$group': {
                    '_id'              : 'total',
                    'beacon_balance'   : {
                        '$sum': 1
                    },
                    'execution_balance': {
                        '$sum': "$execution_balance"
                    }
                }
            }
        ]).to_list(1)
        if tmp:
            data["Total ETH Locked"]["Minipools Stake"]["Pending Minipools"]["_val"] = tmp[0]["beacon_balance"] + tmp[0][
                "execution_balance"]

        # Dissolved Minipools:
        # Minipools that are flagged as dissolved are Pending minipools that didn't trigger the second phase within the configured
        # LaunchTimeout (14 days at the time of writing).
        # They have the following applied to them:
        # - They have 1 ETH locked on the Beacon Chain, not earning any rewards.
        # - The 31 ETH that was waiting in their address was moved back to the Deposit Pool (This can cause the Deposit Pool
        #   to grow beyond its Cap, check the bellow comment for information about that).
        tmp = await self.db.minipools_new.aggregate([
            {
                '$match': {
                    'status': 'dissolved',
                    'vacant': False
                }
            }, {
                '$group': {
                    '_id'              : 'total',
                    'beacon_balance'   : {
                        '$sum': '$beacon.balance'
                    },
                    'execution_balance': {
                        '$sum': '$execution_balance'
                    }
                }
            }
        ]).to_list(1)
        if len(tmp) > 0:
            tmp = tmp[0]
            data["Total ETH Locked"]["Minipools Stake"]["Dissolved Minipools"]["Locked on Beacon Chain"]["_val"] = tmp[
                "beacon_balance"]
            data["Total ETH Locked"]["Minipools Stake"]["Dissolved Minipools"]["Contract Balance"]["_val"] = tmp[
                "execution_balance"]

        # Staking Minipools:
        minipools = await self.db.minipools_new.find({
            'status': {"$nin": ["initialised", "prelaunch", "dissolved"]},
            'node_deposit_balance': {"$exists": True},
        }).to_list(None)

        for minipool in minipools:
            node_share = minipool["node_deposit_balance"] / 32
            commission = minipool["node_fee"]
            refund_balance = minipool["node_refund_balance"]
            contract_balance = minipool["execution_balance"]
            beacon_balance = minipool["beacon"]["balance"]
            # if there is a refund_balance, we first try to pay that off using the contract balance
            if refund_balance > 0:
                if contract_balance > 0:
                    if contract_balance >= refund_balance:
                        contract_balance -= refund_balance
                        data["Total ETH Locked"]["Undistributed Balances"]["Minipool Contract Balances"]["Node Share"][
                            "_val"] += refund_balance
                        refund_balance = 0
                    else:
                        refund_balance -= contract_balance
                        data["Total ETH Locked"]["Undistributed Balances"]["Minipool Contract Balances"]["Node Share"][
                            "_val"] += contract_balance
                        contract_balance = 0
                # if there is still a refund balance, we try to pay it off using the beacon balance
                if refund_balance > 0:
                    if beacon_balance > 0:
                        if beacon_balance >= refund_balance:
                            beacon_balance -= refund_balance
                            data["Total ETH Locked"]["Minipools Stake"]["Staking Minipools"]["Node Share"][
                                "_val"] += refund_balance
                            refund_balance = 0
                        else:
                            refund_balance -= beacon_balance
                            data["Total ETH Locked"]["Minipools Stake"]["Staking Minipools"]["Node Share"][
                                "_val"] += beacon_balance
                            beacon_balance = 0
            beacon_rewards = max(0, beacon_balance - 32)
            if beacon_balance > 0:
                d = split_rewards_logic(beacon_balance, node_share, commission, force_base=True)
                data["Total ETH Locked"]["Minipools Stake"]["Staking Minipools"]["Node Share"]["_val"] += d["base"]["node"]
                data["Total ETH Locked"]["Minipools Stake"]["Staking Minipools"]["rETH Share"]["_val"] += d["base"]["reth"]
                data["Total ETH Locked"]["Undistributed Balances"]["Beacon Chain Rewards"]["Node Share"]["_val"] += \
                    d["rewards"]["node"]
                data["Total ETH Locked"]["Undistributed Balances"]["Beacon Chain Rewards"]["rETH Share"]["_val"] += \
                    d["rewards"]["reth"]
            if contract_balance > 0:
                d = split_rewards_logic(contract_balance, node_share, commission)
                data["Total ETH Locked"]["Undistributed Balances"]["Minipool Contract Balances"]["Node Share"][
                    "_val"] += d["base"]["node"] + d["rewards"]["node"]
                data["Total ETH Locked"]["Undistributed Balances"]["Minipool Contract Balances"]["rETH Share"][
                    "_val"] += d["base"]["reth"] + d["rewards"]["reth"]

        # Deposit Pool Balance: calls the contract and asks what its balance is, simple enough.
        # ETH in here has been swapped for rETH and is waiting to be matched with a minipool.
        # Fun Fact: This value can go above the configured Deposit Pool Cap in 2 scenarios:
        #  - A Minipool gets dissolved, moving 16 ETH from its address back to the Deposit Pool.
        #  - ETH from withdrawn Minipools, which gets stored in the rETH contract, surpasses the configured targetCollateralRate,
        #    which is 10% at the time of writing. Once this occurs the ETH gets moved from the rETH contract to the Deposit Pool.
        data["Total ETH Locked"]["rETH Collateral"]["Deposit Pool"]["_val"] = solidity.to_float(
            rp.call("rocketDepositPool.getBalance"))

        # Extra Collateral: This is ETH stored in the rETH contract from Minipools that have been withdrawn from.
        # This value has a cap - read the above comment for more information about that.
        data["Total ETH Locked"]["rETH Collateral"]["Extra Collateral"]["_val"] = solidity.to_float(
            w3.eth.getBalance(rp.get_address_by_name("rocketTokenRETH")))

        # Smoothing Pool Balance: This is ETH from Proposals by minipools that have joined the Smoothing Pool.
        smoothie_balance = solidity.to_float(w3.eth.getBalance(rp.get_address_by_name("rocketSmoothingPool")))
        tmp = await self.db.node_operators_new.aggregate([
            {
                '$match': {
                    'smoothing_pool_registration_state': True,
                    'staking_minipool_count'           : {
                        '$ne': 0
                    }
                }
            }, {
                '$project': {
                    'staking_minipool_count': 1,
                    'effective_node_share'  : 1,
                    'node_share'            : {
                        '$sum': [
                            '$effective_node_share', {
                                '$multiply': [
                                    {
                                        '$subtract': [
                                            1, '$effective_node_share'
                                        ]
                                    }, '$average_node_fee'
                                ]
                            }
                        ]
                    }
                }
            }, {
                '$group': {
                    '_id'       : None,
                    'node_share': {
                        '$sum': {
                            '$multiply': [
                                '$node_share', '$staking_minipool_count', '$effective_node_share'
                            ]
                        }
                    },
                    'count'     : {
                        '$sum': {
                            '$multiply': [
                                '$staking_minipool_count', '$effective_node_share'
                            ]
                        }
                    }
                }
            }, {
                '$project': {
                    'avg_node_share': {
                        '$divide': [
                            '$node_share', '$count'
                        ]
                    }
                }
            }
        ]).to_list(None)
        if len(tmp) > 0:
            data["Total ETH Locked"]["Undistributed Balances"]["Smoothing Pool Balance"]["Node Share"][
                "_val"] = smoothie_balance * tmp[0]["avg_node_share"]
            data["Total ETH Locked"]["Undistributed Balances"]["Smoothing Pool Balance"]["rETH Share"][
                "_val"] = smoothie_balance * (1 - tmp[0]["avg_node_share"])

        # Unclaimed Smoothing Pool Rewards: This is ETH from the previous Reward Periods that have not been claimed yet.
        data["Total ETH Locked"]["Unclaimed Rewards"]["Smoothing Pool"]["_val"] = solidity.to_float(
            rp.call("rocketVault.balanceOf", "rocketMerkleDistributorMainnet"))

        # Staked RPL: This is all ETH that has been staked by Node Operators.
        data["Total RPL Locked"]["Staked RPL"]["Node Operators"]["_val"] = solidity.to_float(
            rp.call("rocketNodeStaking.getTotalRPLStake"))

        # oDAO bonded RPL: RPL oDAO Members have to lock up to join it. This RPL can be slashed if they misbehave.
        data["Total RPL Locked"]["Staked RPL"]["oDAO Bond"]["_val"] = solidity.to_float(
            rp.call("rocketVault.balanceOfToken", "rocketDAONodeTrustedActions", rpl_address))

        # Unclaimed RPL Rewards: RPL rewards that have been earned by Node Operators but have not been claimed yet.
        data["Total RPL Locked"]["Unclaimed Rewards"]["Node Operators & oDAO"]["_val"] = solidity.to_float(
            rp.call("rocketVault.balanceOfToken", "rocketMerkleDistributorMainnet", rpl_address))

        # Undistributed pDAO Rewards: RPL rewards that have been earned by the pDAO but have not been distributed yet.
        data["Total RPL Locked"]["Unclaimed Rewards"]["pDAO"]["_val"] = solidity.to_float(
            rp.call("rocketVault.balanceOfToken", "rocketClaimDAO", rpl_address))

        # Unused Inflation: RPL that has been minted but not yet been used for rewards.
        # This is (or was) an issue as the snapshots didn't account for the last day of inflation.
        # Joe is already looking into this.
        data["Total RPL Locked"]["Unused Inflation"]["_val"] = solidity.to_float(
            rp.call("rocketVault.balanceOfToken", "rocketRewardsPool", rpl_address))

        # Slashed RPL: RPL that is slashed gets moved to the Auction Manager Contract.
        # This RPL will be sold using a Dutch Auction for ETH, which the gets moved to the rETH contract to be used as
        # extra rETH collateral.
        data["Total RPL Locked"]["Slashed RPL"]["_val"] = solidity.to_float(
            rp.call("rocketVault.balanceOfToken", "rocketAuctionManager", rpl_address))

        # create _value string for each branch. the _value is the sum of all _val or _val values in the children
        tmp = await self.db.node_operators_new.aggregate([
            {
                '$match': {
                    'fee_distributor_eth_balance': {
                        '$gt': 0
                    }
                }
            }, {
                '$project': {
                    'fee_distributor_eth_balance': 1,
                    'node_share'                 : {
                        '$sum': [
                            '$effective_node_share', {
                                '$multiply': [
                                    {
                                        '$subtract': [
                                            1, '$effective_node_share'
                                        ]
                                    }, '$average_node_fee'
                                ]
                            }
                        ]
                    }
                }
            }, {
                '$project': {
                    'node_share': {
                        '$multiply': [
                            '$fee_distributor_eth_balance', '$node_share'
                        ]
                    },
                    'reth_share': {
                        '$multiply': [
                            '$fee_distributor_eth_balance', {
                                '$subtract': [
                                    1, '$node_share'
                                ]
                            }
                        ]
                    }
                }
            }, {
                '$group': {
                    '_id'       : None,
                    'node_share': {
                        '$sum': '$node_share'
                    },
                    'reth_share': {
                        '$sum': '$reth_share'
                    }
                }
            }
        ]).to_list(None)
        if len(tmp) > 0:
            data["Total ETH Locked"]["Undistributed Balances"]["Node Distributor Contracts"]["Node Share"]["_val"] = tmp[0][
                "node_share"]
            data["Total ETH Locked"]["Undistributed Balances"]["Node Distributor Contracts"]["rETH Share"]["_val"] = tmp[0][
                "reth_share"]

        def set_val_of_branch(branch, unit):
            val = 0
            for child in branch:
                if isinstance(branch[child], dict):
                    branch[child]["_val"] = set_val_of_branch(branch[child], unit)
                    branch[child]["_value"] = f"{branch[child]['_val']:,.2f} {unit}"
                    if branch[child].get("_is_estimate", False):
                        branch[child]["_value"] = f"~{branch[child]['_value']}"
                    val += branch[child]["_val"]
                elif not child.startswith("_") or child == "_val":
                    val += branch[child]
            branch["_val"] = val
            branch["_value"] = f"{val:,.2f} {unit}"
            if branch.get("_is_estimate", False):
                branch["_value"] = f"~{branch['_value']}"
            return val

        set_val_of_branch(data["Total ETH Locked"], "ETH")
        set_val_of_branch(data["Total RPL Locked"], "RPL")
        # calculate total tvl
        total_tvl = data["Total ETH Locked"]["_val"] + (data["Total RPL Locked"]["_val"] * rpl_price)
        dai_total_tvl = total_tvl * eth_price
        data["_value"] = f"{total_tvl:,.2f} ETH"
        test = render_tree(data, "Total Locked Value", max_depth=0 if show_all else 2)
        # send embed with tvl
        e = Embed()
        closer = f"or about {Style.BRIGHT}{humanize.intword(dai_total_tvl, format='%.3f')} DAI{Style.RESET_ALL}".rjust(max([len(line) for line in test.split("\n")])-1)
        e.description = f"```ansi\n{test}\n{closer}```"
        e.set_footer(text="\"that looks good to me\" - invis 2023")
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(TVL(bot))
