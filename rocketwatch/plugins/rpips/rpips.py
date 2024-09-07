import logging
import requests

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from discord.app_commands import Choice
from cachetools.func import ttl_cache
from bs4 import BeautifulSoup

from utils.cfg import cfg

log = logging.getLogger("rpips")
log.setLevel(cfg["log_level"])


class RPIPs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def rpip(self, ctx: Context, name: str):
        url = self.get_rpips()[name]
        await ctx.send(url)

    @rpip.autocomplete("name")
    async def get_rpip_names(self, ctx: Context, current: str):
        names = self.get_rpips().keys()
        return [Choice(name=name, value=name) for name in names if current.lower() in name.lower()][:-10:-1]

    @ttl_cache(ttl=300)
    def get_rpips(self) -> dict[str, str]:
        html_doc = requests.get("https://rpips.rocketpool.net/all").text
        soup = BeautifulSoup(html_doc, "html.parser")
        rpips: dict[str, str] = {}

        for row in soup.table.find_all("tr", recursive=False):
            rpip_num = int(row.find("td", {"class": "rpipnum"}).text)
            rpip_title = row.find("td", {"class": "title"}).text.strip()
            title = f"RPIP-{rpip_num}: {rpip_title}"
            url = f"https://rpips.rocketpool.net/RPIPs/RPIP-{rpip_num}"
            rpips[title] = url

        return rpips


async def setup(bot):
    await bot.add_cog(RPIPs(bot))
