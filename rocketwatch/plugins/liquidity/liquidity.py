import logging
from io import BytesIO

import inflect
import matplotlib.pyplot as plt
from discord import File
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.thegraph import scan_nodes
from utils.visibility import is_hidden

log = logging.getLogger("liquidity")
log.setLevel(cfg["log_level"])
p = inflect.engine()


class Liquidity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def withdrawable_rpl(self,
                               ctx: Context):
        """
        Show the available liquidity at different RPL/ETH prices
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        img = BytesIO()

        # get data from thegraph
        data = scan_nodes(["rplStaked", "stakingMinipools"])
        rpl_eth_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))

        # calculate withdrawable RPL at various RPL ETH prices
        # i/10 is the ratio of the price checked to the actual RPL ETH price

        free_rpl_liquidity = {}
        max_collateral = 1.5

        for i in range(1, 31):

            test_ratio = (i / 10)
            rpl_eth_test_price = rpl_eth_price * test_ratio
            liquid_rpl = 0

            for node in data:

                minipool_worth = int(node["stakingMinipools"]) * 16
                rpl_stake = solidity.to_float(node["rplStaked"])

                # if there are no pools, then all the RPL can be withdrawn
                if minipool_worth == 0:
                    liquid_rpl += rpl_stake
                    continue

                effective_staked = rpl_stake * rpl_eth_test_price
                collateral_percentage = effective_staked / minipool_worth

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
                     (rpl_eth_price, current_withdrawable_rpl), textcoords="offset points", xytext=(10, -5), ha='left')
        plt.grid()

        ax = plt.gca()
        ax.set_ylabel("Withdrawable RPL")
        ax.set_xlabel("RPL / ETH ratio")
        ax.yaxis.set_major_formatter(lambda x, _: "{:.1f}m".format(x / 1000000))

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
    await bot.add_cog(Liquidity(bot))
