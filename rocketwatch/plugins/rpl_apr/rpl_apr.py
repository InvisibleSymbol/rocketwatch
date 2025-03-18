import logging
from io import BytesIO

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

log = logging.getLogger("rpl_apr")
log.setLevel(cfg["log_level"])


class RplApr(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb.uri"]).get_database("rocketwatch")

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



async def setup(bot):
    await bot.add_cog(RplApr(bot))
