import logging

import humanize
from discord.commands import slash_command
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("tvl")
log.setLevel(cfg["log_level"])


class TVL(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @slash_command(guild_ids=guilds)
    async def tvl(self, ctx):
        await ctx.defer(ephemeral=is_hidden(ctx))
        tvl = []
        description = []
        eth_price = rp.get_dai_eth_price()
        rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        rpl_address = rp.get_address_by_name("rocketTokenRPL")
        minipool_count_per_status = rp.get_minipool_count_per_status()

        log.debug(minipool_count_per_status)
        tvl.append(minipool_count_per_status[2] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Staking Minipools")

        tmp = await self.db.minipools.aggregate(
            [
                {
                    "$match": {
                        "balance": {"$exists": True},
                    }
                },
                {
                    "$group": {
                        "_id": "total",
                        "total": {"$sum": "$balance"},
                        "count": {"$sum": 1},
                    }
                }
            ]
        ).to_list(length=None)
        rewards = tmp[0]["total"] - (tmp[0]["count"] * 32)
        tvl.append(rewards)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Beacon chain rewards")

        tvl.append(minipool_count_per_status[1] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Pending Minipools")

        tvl.append(minipool_count_per_status[0] * 16)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Unmatched Minipools")

        tvl.append(minipool_count_per_status[3] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Withdrawable Minipools")

        tvl.append(solidity.to_float(rp.call("rocketDepositPool.getBalance")))
        description.append(f"+ {tvl[-1]:12.2f} ETH: Deposit Pool Balance")

        tvl.append(solidity.to_float(w3.eth.getBalance(rp.get_address_by_name("rocketTokenRETH"))))
        description.append(f"+ {tvl[-1]:12.2f} ETH: rETH Extra Collateral")

        description.append("Total ETH Locked".center(max(len(d) for d in description), "-"))
        eth_tvl = sum(tvl)
        # get eth tvl in dai
        dai_eth_tvl = eth_tvl * eth_price
        description.append(f"  {eth_tvl:12.2f} ETH ({humanize.intword(dai_eth_tvl)} DAI)")

        tvl.append(solidity.to_float(rp.call("rocketNodeStaking.getTotalRPLStake")))
        description.append(f"+ {tvl[-1]:12.2f} RPL: Staked RPL")
        # convert rpl to eth for correct tvl calcuation
        tvl[-1] *= rpl_price

        tvl.append(solidity.to_float(rp.call("rocketVault.balanceOfToken", "rocketDAONodeTrustedActions", rpl_address)))
        description.append(f"+ {tvl[-1]:12.2f} RPL: oDAO Bonded RPL")
        # convert rpl to eth for correct tvl calculation
        tvl[-1] *= rpl_price

        tvl.append(solidity.to_float(rp.call("rocketVault.balanceOfToken", "rocketAuctionManager", rpl_address)))
        description.append(f"+ {tvl[-1]:12.2f} RPL: Slashed RPL ")
        # convert rpl to eth for correct tvl calculation
        tvl[-1] *= rpl_price

        description.append("Total Value Locked".center(max(len(d) for d in description), "-"))
        total_tvl = sum(tvl)
        dai_total_tvl = total_tvl * eth_price
        description.append(f"  {total_tvl:12.2f} ETH ({humanize.intword(dai_total_tvl)} DAI)")

        description = "```diff\n" + "\n".join(description) + "```"
        # send embed with tvl
        e = Embed()
        e.set_footer(text="\"that looks good to me\" - kanewallmann 2021")
        e.description = description
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(TVL(bot))
