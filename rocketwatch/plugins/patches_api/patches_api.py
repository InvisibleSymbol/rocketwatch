import logging

import humanize
import requests
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from ens import InvalidName
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, ens
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.visibility import is_hidden

log = logging.getLogger("effective_rpl")
log.setLevel(cfg["log_level"])


class PatchesAPI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @hybrid_command()
    async def get_upcoming_rewards(self, ctx: Context, node_address: str):
        """
        Show the effective RPL staked by users
        """
        await ctx.defer(ephemeral=True)
        ens_name = None
        address = None
        if "." in node_address:
            try:
                address = ens.resolve_name(node_address)
                if not address:
                    await ctx.send("ENS name not found")
                    return
                ens_name = node_address
            except InvalidName:
                await ctx.send("Invalid ENS name")
                return
        else:
            try:
                address = w3.toChecksumAddress(node_address)
                ens_name = ens.get_name(address)
            except InvalidName:
                await ctx.send("Invalid address")
                return
        try:
            patches_res = requests.get(f"https://sprocketpool.net/api/node/{address}").json()
        except Exception as e:
            await report_error(ctx, e)
            await ctx.send("Error fetching node data from SprocketPool API. Blame Patches.")
            return
        e = Embed()
        e.title = f"Upcoming rewards for {ens_name or address}"
        estimated_end_time = patches_res["startTime"] + rp.call("rocketDAOProtocolSettingsRewards.getRewardsClaimIntervalTime")
        e.description = f"Values based upon data from <t:{patches_res['time']}:R> (<t:{patches_res['time']}>).\nThis is for Interval {patches_res['interval']}," \
                        f" which ends <t:{estimated_end_time}:R> (<t:{estimated_end_time}>)."
        if "collateralRpl" not in patches_res[address]:
            await ctx.send("No data found for this node")
            return
        e.add_field(name="Collateral based rewards:", value=f"{solidity.to_float(patches_res[address]['collateralRpl']):,.3f} RPL")
        e.add_field(name="Smoothing pool rewards:", value=f"{solidity.to_float(patches_res[address]['smoothingPoolEth']):,.3f} ETH")
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(PatchesAPI(bot))
