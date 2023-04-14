import logging

import humanize
from discord.app_commands import describe
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.visibility import is_hidden

log = logging.getLogger("tvl")
log.setLevel(cfg["log_level"])


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
        tvl = []
        description = []
        eth_price = rp.get_dai_eth_price()
        rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        rpl_address = rp.get_address_by_name("rocketTokenRPL")
        minipool_count_per_status = rp.get_minipool_count_per_status()
        log.debug(minipool_count_per_status)

        # Staking minipools: stakingCount of minipool_count_per_status * 32 ETH.
        # This is all minipools that have deposited both parts of 16 ETH to the Beacon Chain.
        tvl.append(minipool_count_per_status["stakingCount"] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Staking Minipools")

        # Beacon Chain Rewards: Subtract the sum of ETH on the beacon chain by their base balance (32 ETH).
        # We can't use stakingCount of minipool_count_per_status because the beacon chain has a delayed view due to
        # the eth1 follow distance. So we use the total number of minipools in the database instead.
        # Since we only care about rewards, we ignore all minipools with a balance bellow 32 ETH.
        tmp = await self.db.minipools.aggregate(
            [
                {
                    "$match": {
                        "balance": {"$gt": 32, "$exists": True},
                    }
                },
                {
                    "$group": {
                        "_id"  : "total",
                        "total": {"$sum": "$balance"},
                        "count": {"$sum": 1},
                    }
                }
            ]
        ).to_list(length=None)
        log.debug(f"tmp: {tmp}")
        rewards = tmp[0]["total"] - (tmp[0]["count"] * 32)
        tvl.append(rewards)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Beacon Chain Rewards")

        # Pending Minipools: prelaunchCount of minipool_count_per_status * 32 ETH.
        # Minipools that are flagged as prelaunch have the following applied to them:
        #  - They have deposited 16 ETH to the Beacon Chain.
        #  - They have 16 ETH from the Deposit Pool (or from their node operator if the queue was skipped) in their address
        #    waiting to be staked as well.
        #  - They are currently in the scrubbing process (should be 12 hours) or have not yet initiated the second phase.
        tvl.append(minipool_count_per_status["prelaunchCount"] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Pending Minipools")

        # Unmatched Minipools: initialisedCount of minipool_count_per_status * 32 ETH.
        # Minipools that are flagged as initialised have the following applied to them:
        # - They have 16 ETH from the node operator in their address waiting to be staked.
        # - They have not yet received 16 ETH from the Deposit Pool.
        tvl.append(minipool_count_per_status["initialisedCount"] * 16)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Unmatched Minipools")

        # Withdrawable Minipools: withdrawableCount of minipool_count_per_status * 32 ETH.
        # Minipools that are flagged as withdrawable have the following applied to them:
        # - They don't (or shouldn't) have any ETH on the beacon chain.
        # - The withdrawn ETH should be waiting in their address.
        # To give an accurate number of this we would have to either scrape all the addresses for their Balance or track some
        # Event the amount they have withdrawn. Since this hasn't been implemented, a flat 32 ETH is assumed.
        tvl.append(minipool_count_per_status["withdrawableCount"] * 32)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Withdrawable Minipools")

        # Dissolved Minipools: dissolvedCount of minipool_count_per_status * 16 ETH.
        # Minipools that are flagged as dissolved are Pending minipools that didn't trigger the second phase within the configured
        # LaunchTimeout (14 days at the time of writing).
        # They have the following applied to them:
        # - They have 16 ETH locked on the Beacon Chain, not earning any rewards.
        # - The 16 ETH that was waiting in their address was moved back to the Deposit Pool, assuming it wasn't a 32 ETH minipool,
        #   in which case no ETH gets moved.(This can cause the Deposit Pool to grow beyond its Cap, check the bellow comment
        #   for information about that).
        # The latter means it can have either 16 ETH or 32 ETH locked in this state. The current implementation assumes 16 ETH.
        # TODO fix the above comment.
        tvl.append(minipool_count_per_status["dissolvedCount"] * 16)
        description.append(f"+ {tvl[-1]:12.2f} ETH: Dissolved Minipools")

        # Deposit Pool Balance: calls the contract and asks what its balance is, simple enough.
        # ETH in here has been swapped for rETH and is waiting to be matched with a minipool.
        # Fun Fact: This value can go above the configured Deposit Pool Cap in 2 scenarios:
        #  - A Minipool gets dissolved, moving 16 ETH from its address back to the Deposit Pool.
        #  - ETH from withdrawn Minipools, which gets stored in the rETH contract, surpasses the configured targetCollateralRate,
        #    which is 10% at the time of writing. Once this occurs the ETH gets moved from the rETH contract to the Deposit Pool.
        tvl.append(solidity.to_float(rp.call("rocketDepositPool.getBalance")))
        description.append(f"+ {tvl[-1]:12.2f} ETH: Deposit Pool Balance")

        # Extra Collateral: This is ETH stored in the rETH contract from Minipools that have been withdrawn from.
        # This value has a cap - read the above comment for more information about that.
        tvl.append(solidity.to_float(w3.eth.getBalance(rp.get_address_by_name("rocketTokenRETH"))))
        description.append(f"+ {tvl[-1]:12.2f} ETH: rETH Extra Collateral")

        description.append("Total ETH Locked".center(max(len(d) for d in description), "-"))
        eth_tvl = sum(tvl)
        # get eth tvl in dai
        dai_eth_tvl = eth_tvl * eth_price
        description.append(f"  {eth_tvl:12.2f} ETH ({humanize.intword(dai_eth_tvl)} DAI)")

        # Staked RPL: This is all ETH that has been staked by Node Operators.
        tvl.append(solidity.to_float(rp.call("rocketNodeStaking.getTotalRPLStake")))
        description.append(f"+ {tvl[-1]:12.2f} RPL: Staked RPL")
        # convert rpl to eth for correct tvl calcuation
        tvl[-1] *= rpl_price

        # oDAO bonded RPL: RPL oDAO Members have to lock up to join it. This RPL can be slashed if they misbehave.
        tvl.append(solidity.to_float(rp.call("rocketVault.balanceOfToken", "rocketDAONodeTrustedActions", rpl_address)))
        description.append(f"+ {tvl[-1]:12.2f} RPL: oDAO Bonded RPL")
        # convert rpl to eth for correct tvl calculation
        tvl[-1] *= rpl_price

        # Slashed RPL: RPL that is slashed gets moved to the Auction Manager Contract.
        # This RPL will be sold using a Dutch Auction for ETH, which the gets moved to the rETH contract to be used as
        # extra rETH collateral.
        tvl.append(solidity.to_float(rp.call("rocketVault.balanceOfToken", "rocketAuctionManager", rpl_address)))
        description.append(f"+ {tvl[-1]:12.2f} RPL: Slashed RPL ")
        # convert rpl to eth for correct tvl calculation
        tvl[-1] *= rpl_price

        description.append("Total Value Locked".center(max(len(d) for d in description), "-"))
        total_tvl = sum(tvl)
        dai_total_tvl = total_tvl * eth_price
        description.append(f"  {total_tvl:12.2f} ETH ({humanize.intword(dai_total_tvl)} DAI)")

        description = "```diff\n" + "\n".join([d for d in description if " 0.00 " not in d or show_all]) + "```"

        # add temporary warning at the end of description that we arent account for minipool balances on the execution layer yet
        description += "\n**WARNING**: Minipool balances on the execution layer are not yet accounted for in the above TVL."
        # send embed with tvl
        e = Embed()
        e.set_footer(text="\"that looks good to me\" - kanewallmann 2021")
        e.description = description
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(TVL(bot))
