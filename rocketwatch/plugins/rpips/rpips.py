import logging
import requests
from typing import Optional, Any

from bs4 import BeautifulSoup
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from discord.app_commands import Choice, describe
from cachetools.func import ttl_cache

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed
from utils.retry import retry

log = logging.getLogger("rpips")
log.setLevel(cfg["log_level"])


class RPIPs(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @hybrid_command()
    @describe(name="RPIP name")
    async def rpip(self, ctx: Context, name: str):
        """Show information about a specific RPIP."""
        await ctx.defer()
        embed = Embed()
        embed.set_author(name="🔗 Data from rpips.rocketpool.net", url="https://rpips.rocketpool.net")

        rpips_by_name: dict[str, RPIPs.RPIP] = {str(rpip): rpip for rpip in self.get_all_rpips()}
        if rpip := rpips_by_name.get(name):
            embed.title = name
            embed.url = rpip.url
            embed.description = rpip.description

            if len(rpip.authors) == 1:
                embed.add_field(name="Author", value=rpip.authors[0])
            else:
                embed.add_field(name="Authors", value=", ".join(rpip.authors))

            embed.add_field(name="Status", value=rpip.status)
            embed.add_field(name="Created", value=rpip.created)
            embed.add_field(name="Discussion Link", value=rpip.discussion, inline=False)
        else:
            embed.description = "No matching RPIPs."

        await ctx.send(embed=embed)

    class RPIP:
        def __init__(self, title: str, number: int, status:str):
            self.title = title
            self.number = number
            self.status = status

        @ttl_cache(ttl=300)
        @retry(tries=3, delay=1)
        def __fetch_data(self) -> dict[str, Optional[str | list[str]]]:
            soup = BeautifulSoup(requests.get(self.url).text, "html.parser")
            metadata = {}

            for field in soup.main.find("table", {"class": "rpip-preamble"}).find_all("tr"):
                match field_name := field.th.text:
                    case "Discussion":
                        metadata[field_name] = field.td.a["href"]
                    case "Author":
                        metadata[field_name] = [a.text for a in field.td.find_all("a")]
                    case _:
                        metadata[field_name] = field.td.text

            return {
                "type": metadata.get("Type"),
                "authors": metadata.get("Author"),
                "created": metadata.get("Created"),
                "discussion": metadata.get("Discussion"),
                "description": soup.find("big", {"class": "rpip-description"}).text
            }

        @property
        def url(self) -> str:
            return f"https://rpips.rocketpool.net/RPIPs/RPIP-{self.number}"

        def __str__(self) -> str:
            return f"RPIP-{self.number}: {self.title}"

        def __getattr__(self, key: str) -> Any:
            try:
                return self.__fetch_data()[key] or "N/A"
            except KeyError:
                raise AttributeError(f"RPIP has no attribute '{key}'")

    @rpip.autocomplete("name")
    async def _get_rpip_names(self, ctx: Context, current: str) -> list[Choice[str]]:
        choices = []
        for rpip in self.get_all_rpips():
            if current.lower() in (name := str(rpip)).lower():
                choices.append(Choice(name=name, value=name))
        return choices[:-26:-1]

    @staticmethod
    @ttl_cache(ttl=60)
    @retry(tries=3, delay=1)
    def get_all_rpips() -> list['RPIPs.RPIP']:
        html_doc = requests.get("https://rpips.rocketpool.net/all").text
        soup = BeautifulSoup(html_doc, "html.parser")
        rpips: list['RPIPs.RPIP'] = []

        for row in soup.table.find_all("tr", recursive=False):
            title = row.find("td", {"class": "title"}).text.strip()
            rpip_num = int(row.find("td", {"class": "rpipnum"}).text)
            status = row.find("td", {"class": "status"}).text.strip()
            rpips.append(RPIPs.RPIP(title, rpip_num, status))

        return rpips


async def setup(bot):
    await bot.add_cog(RPIPs(bot))
