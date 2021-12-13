import os
import time

import humanize
import psutil
import requests
import uptime
from discord import Embed
from discord.commands import slash_command
from discord.ext import commands

from utils import readable
from utils.cfg import cfg
from utils.readable import etherscan_url
from utils.reporter import report_error
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

psutil.getloadavg()
BOOT_TIME = time.time()


class About(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.process = psutil.Process(os.getpid())

    @slash_command(guild_ids=guilds)
    async def about(self, ctx):
        """Bot and Server Information"""
        await ctx.defer(ephemeral=is_hidden(ctx))
        embed = Embed()
        g = self.bot.guilds
        code_time = None

        if cfg.get("wakatime.secret"):
            try:
                code_time = requests.get("https://wakatime.com/api/v1/users/current/all_time_since_today",
                                         params={
                                             "project": "rocketwatch",
                                             "api_key": cfg["wakatime.secret"]
                                         }).json()["data"]["text"]
            except Exception as err:
                await report_error(err)

        if code_time:
            embed.add_field(name="Project Statistics",
                            value=f"An estimate of {code_time} has been spent developing this bot!",
                            inline=False)

        embed.add_field(name="Bot Statistics",
                        value=f"{len(g)} Guilds joined and "
                              f"{humanize.intcomma(sum(guild.member_count for guild in g))} Members reached!",
                        inline=False)

        address = etherscan_url(cfg["rocketpool.manual_addresses.rocketStorage"])
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

        # show credits
        try:
            contributors = [
                f"[{c['login']}]({c['html_url']}) ({c['contributions']})"
                for c in requests.get("https://api.github.com/repos/InvisibleSymbol/rocketwatch/contributors").json()
                if "bot" not in c["login"].lower()
            ]
            contributors_str = ", ".join(contributors[:5])
            if len(contributors) > 10:
                contributors_str += " and more"
            embed.add_field(name="Contributors", value=contributors_str)
        except Exception as err:
            await report_error(err)

        await ctx.respond(embed=embed, ephemeral=is_hidden(ctx))

    @slash_command(guild_ids=guilds)
    async def donate(self, ctx):
        """Donate to the Bot Developer"""
        embed = Embed()
        embed.title = "Donate to the Developer"
        embed.description = "I hope my bot has been useful to you, it has been a fun experience building it!\n" \
                            "Donations will help me keep doing what I love (and pay the server bills haha)\n\n" \
                            "I accept Donations on all Ethereum related Chains! (Mainnet, Polygon, Rollups, etc.)\n\n" \
                            "**Message me about your Donation for a free POAP!\***\n\n" \
                            "_*as long as supplies last, donate 1 dollar or more to qualify c:_"
        embed.add_field(name="Donation Address",
                        value="[`0xinvis.eth`](https://etherscan.io/address/0xf0138d2e4037957d7b37de312a16a88a7f83a32a)")

        """
        # add address qrcode
        query_string = urlencode({
            "chs" : "128x128",
            "cht" : "qr",
            "chl" : "0xF0138d2e4037957D7b37De312a16a88A7f83A32a",
            "choe": "UTF-8",
            "chld": "L|0"
        })
        embed.set_image(url="https://chart.googleapis.com/chart?" + query_string)
        """
        # POAP Event
        embed.set_image(url="https://i.imgur.com/hNOX3Bj.png")

        embed.set_footer(text="Thank you for your support! <3")
        await ctx.respond(
            embed=embed,
            ephemeral=True)


def setup(bot):
    bot.add_cog(About(bot))
