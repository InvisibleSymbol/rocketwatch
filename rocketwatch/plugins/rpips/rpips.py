import re
import html
import logging
import requests

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from discord.app_commands import Choice
from cachetools.func import ttl_cache

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
        rpip_titles = self.get_rpips().keys()
        return [Choice(name=name, value=name) for name in rpip_titles if current.lower() in name.lower()][:-10:-1]

    @ttl_cache(ttl=300)
    def get_rpips(self) -> dict[str, str]:
        html_text = requests.get("https://rpips.rocketpool.net/all").text

        table_start = html_text.index('<table class="rpiptable">')
        table_end = html_text.index('</table>', table_start)

        offset = html_text.index('</thead>', table_start)
        rpip_dict: dict[str, str] = {}

        while True:
            row_start = html_text.find("<tr>", offset)
            row_end = html_text.find("</tr>", row_start)
            offset = row_end
            if not table_start <= offset <= table_end:
                break

            match = re.search(
                r'<td class="title"><a href="/RPIPs/(?P<num>RPIP-\d*)">(?P<title>.*)</a>',
                html_text[row_start:row_end]
            )
            title = f"{match.group('num')}: {html.unescape(match.group('title'))}"
            url = f"https://rpips.rocketpool.net/RPIPs/{match.group('num')}"
            rpip_dict[title] = url

        return rpip_dict


async def setup(bot):
    await bot.add_cog(RPIPs(bot))
