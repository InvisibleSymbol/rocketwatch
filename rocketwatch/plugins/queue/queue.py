import asyncio
import logging
from datetime import datetime
from io import BytesIO

import humanize
import pytz
from discord import Embed, Color
from discord import File
from discord.ext import commands
from discord.commands import slash_command

from utils import solidity
from utils.cfg import cfg
from utils.deposit_pool_graph import get_graph
from utils.readable import etherscan_url, uptime
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.thegraph import get_average_commission
from utils.visibility import is_hidden

log = logging.getLogger("Queue")
log.setLevel(cfg["log_level"])


class Queue(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def queue(self, ctx):
        """Show the next 10 minipools in the queue"""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(colour=self.color)
        e.title = "Minipool queue"

        # Get the next 10 minipools per category
        minipools = rp.get_minipools(limit=10)
        description = ""
        matchings = [
            ["half", "Normal Minipool Queue"],
            ["full", "Full Minipool Refund Queue"],
            ["empty", "Unbonded Minipool FillingQueue"]
        ]
        for category, label in matchings:
            data = minipools[category]
            if data[1]:
                description += f"**{label}:** ({data[0]} Minipools)"
                description += "\n- "
                description += "\n- ".join([etherscan_url(m, f'`{m}`') for m in data[1]])
                if data[0] > 10:
                    description += "\n- ..."
                description += "\n\n"

        # add explainer at the top of the description if both half and full are not empty
        if minipools["half"][1] and minipools["full"][1]:
            description = "Queues are processed from top to bottom.\n" \
                      "This means that the \"Normal Minipool\" Queue *has to be empty*\n" \
                      "before the \"Full Minipool Refund\" Queue gets processed!\n\n" + description
        
        # set gif if all queus are empty
        if not description:
            e.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
        else:
            e.description = description
        
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(Queue(bot))
