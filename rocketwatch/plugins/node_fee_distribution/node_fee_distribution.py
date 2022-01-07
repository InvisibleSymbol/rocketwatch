import logging
import time
import inflect
import numpy as np
from discord import Embed, Color
from discord.commands import slash_command
from discord.ext import commands

from utils.cfg import cfg
from utils.rocketpool import rp
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

from utils.etherscan import get_recent_account_transactions

log = logging.getLogger("node_fee_distribution")
log.setLevel(cfg["log_level"])
p = inflect.engine()

PERCENTILES = [50, 75, 90, 99]


def get_percentiles(percentiles, values):
    return {p: np.percentile(values, p, interpolation='nearest') for p in percentiles}


class NodeFeeDistribution(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

        self.node_deposit_address = rp.get_address_by_name("rocketNodeDeposit")
        self.rpl_staking_address = rp.get_address_by_name("rocketNodeStaking")

    @slash_command(guild_ids=guilds)
    async def node_fee_distribution(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        deposit_txs = get_recent_account_transactions(
            self.node_deposit_address)
        rpl_staking_txs = get_recent_account_transactions(
            self.rpl_staking_address)

        e = Embed(color=self.color)
        e.title = "Node Fee Distributions"

        if len(deposit_txs) > 0:
            since = min([int(x["timeStamp"]) for x in deposit_txs.values()])
            deposit_gas_percentiles = get_percentiles(
                PERCENTILES, [int(x["gasPrice"]) / 1E9 for x in deposit_txs.values()])
            deposit_fee_percentiles = get_percentiles(PERCENTILES, [int(
                x["gasUsed"]) * int(x["gasPrice"]) / float(1E18) for x in deposit_txs.values()])

            e.description = f"**Minipool Deposit Fees:**\n"
            e.description += f"_Since {time.asctime(time.localtime(since))}_\n"
            for p in PERCENTILES:
                e.description += f"{str(p)}th percentile: {int(deposit_gas_percentiles[p])} gwei gas, {deposit_fee_percentiles[p]:.4f} eth total\n"
        else:
            e.description = "No recent minipool deposit transactions found.\n"

        if len(rpl_staking_txs) > 0:
            since = min([int(x["timeStamp"])
                        for x in rpl_staking_txs.values()])
            rpl_staking_gas_percentiles = get_percentiles(
                PERCENTILES, [int(x["gasPrice"]) / 1E9 for x in rpl_staking_txs.values()])
            rpl_staking_fee_percentiles = get_percentiles(PERCENTILES, [int(
                x["gasUsed"]) * int(x["gasPrice"]) / float(1E18) for x in rpl_staking_txs.values()])

            e.description += f"**RPL Staking Fees:**\n"
            e.description += f"_Since {time.asctime(time.localtime(since))}_\n"
            for p in PERCENTILES:
                e.description += f"{str(p)}th percentile: {int(rpl_staking_gas_percentiles[p])} gwei gas, {rpl_staking_fee_percentiles[p]:.4f} eth total\n"
        else:
            e.description += "No recent RPL stake transactions found.\n"

        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(NodeFeeDistribution(bot))
