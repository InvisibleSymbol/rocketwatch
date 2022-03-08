import logging

import numpy as np
from discord.commands import slash_command
from discord.ext import commands

from utils.cfg import cfg
from utils.embeds import Embed
from utils.etherscan import get_recent_account_transactions
from utils.rocketpool import rp
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("node_fee_distribution")
log.setLevel(cfg["log_level"])


def get_percentiles(percentiles, values):
    return {p: np.percentile(values, p, interpolation='nearest') for p in percentiles}


class NodeFeeDistribution(commands.Cog):
    PERCENTILES = [1, 10, 25, 50, 75, 90, 99]

    def __init__(self, bot):
        self.bot = bot

        self.node_deposit_address = rp.get_address_by_name("rocketNodeDeposit")
        self.rpl_staking_address = rp.get_address_by_name("rocketNodeStaking")

    @slash_command(guild_ids=guilds)
    async def node_fee_distribution(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))

        e = Embed()
        e.title = "Node Fee Distributions"
        e.description = ""

        deposit_txs = await get_recent_account_transactions(
            self.node_deposit_address)
        rpl_staking_txs = await get_recent_account_transactions(
            self.rpl_staking_address)
        first = True

        for title, txs in [('Minipool Deposit', deposit_txs), ('RPL Staking', rpl_staking_txs)]:
            if not first:
                e.description += "\n"
            else:
                first = False

            if len(txs) > 0:
                since = min(int(x["timeStamp"]) for x in txs.values())
                gas = [int(x["gasPrice"]) // int(1E9) for x in txs.values()]
                totals = [int(x["gasUsed"]) * int(x["gasPrice"]) /
                          1E18 for x in txs.values()]
                gas_percentiles = get_percentiles(NodeFeeDistribution.PERCENTILES, gas)
                fee_percentiles = get_percentiles(NodeFeeDistribution.PERCENTILES, totals)

                e.description += f"**{title} Fees:**\n"
                e.description += f"_Since <t:{since}>_\n```"
                e.description += f"Minimum: {min(gas)} gwei gas, {min(totals):.4f} eth total\n"
                for p in NodeFeeDistribution.PERCENTILES:
                    e.description += f"{str(p):>2}th percentile: {int(gas_percentiles[p]):>4} gwei gas, {fee_percentiles[p]:.4f} eth total\n"
                e.description += f"Maximum: {max(gas)} gwei gas, {max(totals):.4f} eth total```\n"
            else:
                e.description += f"No recent {title} transactions found.\n"

        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(NodeFeeDistribution(bot))
