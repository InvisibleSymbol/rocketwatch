import logging
from io import BytesIO

import humanize
import matplotlib.pyplot as plt
import numpy as np
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.get_nearest_block import get_block_by_timestamp
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.visibility import is_hidden

log = logging.getLogger("rpl")
log.setLevel(cfg["log_level"])


class RPL(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb.uri"]).rocketwatch

    @hybrid_command()
    async def rpl_apr(self, ctx: Context):
        """
        Show the RPL APR.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()

        reward_duration = rp.call("rocketRewardsPool.getClaimIntervalTime")
        total_rpl_staked = await self.db.node_operators_new.aggregate([
            {
                '$group': {
                    '_id'                      : 'out',
                    'total_effective_rpl_stake': {
                        '$sum': '$effective_rpl_stake'
                    }
                }
            }
        ]).next()
        total_rpl_staked = total_rpl_staked["total_effective_rpl_stake"]

        # track down the rewards for node operators from the last reward period
        contract = rp.get_contract_by_name("rocketVault")
        m = get_block_by_timestamp(rp.call("rocketRewardsPool.getClaimIntervalTimeStart"))[0]
        events = contract.events["TokenDeposited"].getLogs(argument_filters={
            "by": w3.soliditySha3(
                ["string", "address"],
                ["rocketMerkleDistributorMainnet", rp.get_address_by_name("rocketTokenRPL")])
        }, fromBlock=m - 10000, toBlock=m + 10000)
        perc_nodes = solidity.to_float(rp.call("rocketRewardsPool.getClaimingContractPerc", "rocketClaimNode"))
        perc_odao = solidity.to_float(rp.call("rocketRewardsPool.getClaimingContractPerc", "rocketClaimTrustedNode"))
        node_operator_rewards = solidity.to_float(events[0].args.amount) * (perc_nodes / (perc_nodes + perc_odao))
        if not e:
            raise Exception("no rpl deposit event found")

        xmin = total_rpl_staked * 0.66
        xmax = total_rpl_staked * 1.33
        x = np.linspace(xmin, xmax)

        def apr_curve(staked):
            return (node_operator_rewards / staked) / (reward_duration / 60 / 60 / 24) * 365

        apr = apr_curve(total_rpl_staked)
        y = apr_curve(x)
        fig = plt.figure()
        plt.plot(x, y, color=str(e.color))
        plt.xlim(xmin, xmax)
        plt.ylim(apr_curve(xmax) * 0.9, apr_curve(xmin) * 1.1)
        plt.plot(total_rpl_staked, apr, 'bo')
        plt.annotate(f"{apr:.2%}", (total_rpl_staked, apr),
                     textcoords="offset points", xytext=(-10, -5), ha='right')
        plt.annotate(f"{total_rpl_staked / 1000000:.2f} million staked",
                     (total_rpl_staked, apr), textcoords="offset points", xytext=(10, -5), ha='left')
        plt.grid()

        ax = plt.gca()
        ax.xaxis.set_major_formatter(lambda x, _: "{:.1f}m".format(x / 1000000))
        ax.yaxis.set_major_formatter("{x:.2%}")
        ax.set_ylabel("APR")
        ax.set_xlabel("RPL Staked")
        fig.tight_layout()

        img = BytesIO()
        fig.savefig(img, format='png')
        img.seek(0)
        plt.close()

        e.title = "RPL APR Graph"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        await ctx.send(embed=e, files=[f])
        img.close()

    @hybrid_command()
    async def effective_rpl_staked(self, ctx: Context):
        """
        Show the effective RPL staked by users
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        # get total RPL staked
        total_rpl_staked = solidity.to_float(rp.call("rocketNodeStaking.getTotalRPLStake"))
        e.add_field(name="Total RPL Staked:", value=f"{humanize.intcomma(total_rpl_staked, 2)} RPL", inline=False)
        # get effective RPL staked
        effective_rpl_stake = await self.db.node_operators_new.aggregate([
            {
                '$group': {
                    '_id'                      : 'out',
                    'total_effective_rpl_stake': {
                        '$sum': '$effective_rpl_stake'
                    }
                }
            }
        ]).next()
        effective_rpl_stake = effective_rpl_stake["total_effective_rpl_stake"]        # calculate percentage staked
        percentage_staked = effective_rpl_stake / total_rpl_staked
        e.add_field(name="Effective RPL Staked:", value=f"{humanize.intcomma(effective_rpl_stake, 2)} RPL "
                                                        f"({percentage_staked:.2%})", inline=False)
        # get total supply
        total_rpl_supply = solidity.to_float(rp.call("rocketTokenRPL.totalSupply"))
        # calculate total staked as a percentage of total supply
        percentage_of_total_staked = total_rpl_staked / total_rpl_supply
        e.add_field(name="Percentage of RPL Supply Staked:", value=f"{percentage_of_total_staked:.2%}", inline=False)
        await ctx.send(embed=e)

    @hybrid_command()
    async def withdrawable_rpl(self,
                               ctx: Context):
        """
        Show the available liquidity at different RPL/ETH prices
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        img = BytesIO()

        data = await self.db.node_operators_new.aggregate([
            {
                '$match': {
                    'staking_minipool_count': {
                        '$ne': 0
                    }
                }
            }, {
                '$project': {
                    'ethStake': {
                        '$multiply': [
                            '$effective_node_share', {
                                '$multiply': [
                                    '$staking_minipool_count', 32
                                ]
                            }
                        ]
                    },
                    'rpl_stake': 1
                }
            }
        ]).to_list(length=None)
        rpl_eth_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))

        # calculate withdrawable RPL at various RPL ETH prices
        # i/10 is the ratio of the price checked to the actual RPL ETH price

        free_rpl_liquidity = {}
        max_collateral = solidity.to_float(rp.call("rocketDAOProtocolSettingsNode.getMaximumPerMinipoolStake"))
        current_withdrawable_rpl = 0
        for i in range(1, 31):

            test_ratio = (i / 10)
            rpl_eth_test_price = rpl_eth_price * test_ratio
            liquid_rpl = 0

            for node in data:

                eth_stake = node["ethStake"]
                rpl_stake = node["rpl_stake"]

                # if there are no pools, then all the RPL can be withdrawn
                if eth_stake == 0:
                    liquid_rpl += rpl_stake
                    continue

                effective_staked = rpl_stake * rpl_eth_test_price
                collateral_percentage = effective_staked / eth_stake

                # if there is no extra RPL, go to the next node
                if collateral_percentage < max_collateral:
                    continue

                liquid_rpl += ((collateral_percentage - max_collateral) / collateral_percentage) * rpl_stake

            free_rpl_liquidity[i] = (rpl_eth_test_price, liquid_rpl)
            if test_ratio == 1:
                current_withdrawable_rpl = liquid_rpl

        # break the tuples into lists to plot
        x, y = zip(*list(free_rpl_liquidity.values()))

        # plot the data
        plt.plot(x, y, color=str(e.color))
        plt.plot(rpl_eth_price, current_withdrawable_rpl, 'bo')
        plt.xlim(min(x), max(x))

        plt.annotate(f"{rpl_eth_price:.4f}", (rpl_eth_price, current_withdrawable_rpl),
                     textcoords="offset points", xytext=(-10, -5), ha='right')
        plt.annotate(f"{current_withdrawable_rpl / 1000000:.2f} million RPL withdrawable",
                     (rpl_eth_price, current_withdrawable_rpl), textcoords="offset points", xytext=(10, -5),
                     ha='left')
        plt.grid()

        ax = plt.gca()
        ax.set_ylabel("Withdrawable RPL")
        ax.set_xlabel("RPL / ETH ratio")
        ax.yaxis.set_major_formatter(lambda x, _: "{:.1f}m".format(x / 1000000))
        ax.xaxis.set_major_formatter(lambda x, _: "{:.4f}".format(x))

        plt.tight_layout()
        plt.savefig(img, format='png')
        img.seek(0)

        plt.close()

        e.title = "Available RPL Liquidity"
        e.set_image(url="attachment://graph.png")
        f = File(img, filename="graph.png")
        await ctx.send(embed=e, files=[f])
        img.close()


async def setup(bot):
    await bot.add_cog(RPL(bot))
