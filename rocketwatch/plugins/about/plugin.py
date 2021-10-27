import os
import time

import humanize
import psutil
import uptime
from discord import Embed
from discord.ext import commands
from discord_slash import cog_ext

from utils import readable
from utils.cfg import cfg
from utils.readable import etherscan_url
from utils.slash_permissions import guilds

psutil.getloadavg()
BOOT_TIME = time.time()


class About(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.process = psutil.Process(os.getpid())

    @cog_ext.cog_slash(guild_ids=guilds)
    async def about(self, ctx):
        """Bot and Server Information"""
        embed = Embed()

        g = self.bot.guilds
        embed.add_field(name="Bot Statistics",
                        value=f"{len(g)} Guilds joined and "
                              f"{humanize.intcomma(sum(guild.member_count for guild in g))} Members reached!",
                        inline=False)

        if cfg["rocketpool.chain"] == "mainnet":
            address = "TBA"
        else:
            address = etherscan_url(cfg["rocketpool.storage_contract"])
        embed.add_field(name="Storage Contract", value=address)

        embed.add_field(name="Chain", value=cfg["rocketpool.chain"].capitalize())

        embed.add_field(name="Plugins loaded", value=str(len(self.bot.cogs)))

        embed.add_field(name="Host CPU", value=f"{psutil.cpu_percent():.2f}%")
        embed.add_field(name="Host Memory", value=f"{psutil.virtual_memory().percent}% used")
        embed.add_field(name="Bot Memory", value=f"{humanize.naturalsize(self.process.memory_info().rss)} used")

        load = psutil.getloadavg()
        embed.add_field(name="Host Load", value='/'.join(str(l) for l in load))

        system_uptime = uptime.uptime()
        embed.add_field(name="Host Uptime", value=f"{readable.uptime(system_uptime)}")

        bot_uptime = time.time() - BOOT_TIME
        embed.add_field(name="Bot Uptime", value=f"{readable.uptime(bot_uptime)}")

        await ctx.send(embed=embed)

    @cog_ext.cog_slash(guild_ids=guilds)
    async def donate(self, ctx):
        """Donate to the Bot Developer"""
        embed = Embed()
        embed.description = "Donation Address: **`0x87FF5B8ccFAeEC77b2B4090FD27b11dA2ED808Fb`** ([Ownership Proof](https://etherscan.io/verifySig/3414))"
        embed.set_footer(text="Ethereum or Ethereum-based Rollups preferred, but other chains are ofc fine as well")
        content = "**Thank you for your support! <3**\n" \
                  "It has been a fun experience to work on this bot, and I hope it has been useful to you!\n" \
                  "Any donation helps me keep doing what I love (and pay the server bills lol)!"
        await ctx.send(
            content,
            embed=embed,
            hidden=True)


def setup(bot):
    bot.add_cog(About(bot))
