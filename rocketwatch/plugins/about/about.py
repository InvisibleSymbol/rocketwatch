import os
import time
from urllib.parse import urlencode

import humanize
import psutil
import requests
import uptime
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from rocketwatch import RocketWatch
from utils import readable
from utils.cfg import cfg
from utils.embeds import Embed
from utils.embeds import el_explorer_url
from utils.visibility import is_hidden

psutil.getloadavg()
BOOT_TIME = time.time()


class About(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.process = psutil.Process(os.getpid())

    @hybrid_command()
    async def about(self, ctx: Context):
        """Bot and Server Information"""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        g = self.bot.guilds
        code_time = None

        if api_key := cfg.get("other.wakatime_secret"):
            try:
                code_time = requests.get(
                    "https://wakatime.com/api/v1/users/current/all_time_since_today",
                     params={
                         "project": "rocketwatch",
                         "api_key": api_key
                     }
                ).json()["data"]["text"]
            except Exception as err:
                await self.bot.report_error(err)

        if code_time:
            e.add_field(name="Project Statistics",
                        value=f"An estimate of {code_time} has been spent developing this bot!",
                        inline=False)

        e.add_field(name="Bot Statistics",
                    value=f"{len(g)} Guilds joined and "
                          f"{humanize.intcomma(sum(guild.member_count for guild in g))} Members reached!",
                    inline=False)

        address = el_explorer_url(cfg["rocketpool.manual_addresses.rocketStorage"])
        e.add_field(name="Storage Contract", value=address)

        e.add_field(name="Chain", value=cfg["rocketpool.chain"].capitalize())

        e.add_field(name="Plugins loaded", value=str(len(self.bot.cogs)))

        e.add_field(name="Host CPU", value=f"{psutil.cpu_percent():.2f}%")
        e.add_field(name="Host Memory", value=f"{psutil.virtual_memory().percent}% used")
        e.add_field(name="Bot Memory", value=f"{humanize.naturalsize(self.process.memory_info().rss)} used")

        load = psutil.getloadavg()
        e.add_field(name="Host Load", value='/'.join(str(l) for l in load))

        system_uptime = uptime.uptime()
        e.add_field(name="Host Uptime", value=f"{readable.uptime(system_uptime)}")

        bot_uptime = time.time() - BOOT_TIME
        e.add_field(name="Bot Uptime", value=f"{readable.uptime(bot_uptime)}")

        # show credits
        try:
            contributors = [
                f"[{c['login']}]({c['html_url']}) ({c['contributions']})"
                for c in requests.get("https://api.github.com/repos/InvisibleSymbol/rocketwatch/contributors").json()
                if "bot" not in c["login"].lower()
            ]
            contributors_str = ", ".join(contributors[:10])
            if len(contributors) > 10:
                contributors_str += " and more"
            e.add_field(name="Contributors", value=contributors_str)
        except Exception as err:
            await self.bot.report_error(err)

        await ctx.send(embed=e)

    @hybrid_command()
    async def donate(self, ctx: Context):
        """Donate to the Bot Developer"""
        await ctx.defer(ephemeral=True)
        e = Embed()
        e.title = "Donate to the Developer"
        e.description = "I hope my bot has been useful to you, it has been a fun experience building it!\n" \
                        "Donations will help me keep doing what I love (and pay the server bills haha)\n\n" \
                        "I accept Donations on all Ethereum related Chains! (Mainnet, Polygon, Rollups, etc.)"
        e.add_field(name="Donation Address",
                    value="[`0xinvis.eth`](https://etherscan.io/address/0xf0138d2e4037957d7b37de312a16a88a7f83a32a)")

        # add address qrcode
        query_string = urlencode({
            "chs" : "128x128",
            "cht" : "qr",
            "chl" : "0xF0138d2e4037957D7b37De312a16a88A7f83A32a",
            "choe": "UTF-8",
            "chld": "L|0"
        })
        e.set_image(url=f"https://chart.googleapis.com/chart?{query_string}")

        e.set_footer(text="Thank you for your support! <3")
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(About(bot))
