import logging
import requests
from typing import Union

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from discord.app_commands import Choice, describe
from cachetools.func import ttl_cache
from bs4 import BeautifulSoup

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed

log = logging.getLogger("rpips")
log.setLevel(cfg["log_level"])


class RPIPS(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @hybrid_command()
    @describe(name="RPIP name")
    async def rpip(self, ctx: Context, name: str):
        """Show information about a specific RPIP."""
        await ctx.defer()
        embed = Embed()
        embed.set_author(name="🔗 Data from rpips.rocketpool.net", url="https://rpips.rocketpool.net")

        if rpip := self.get_rpips().get(name):
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
        def __init__(self, url: str):
            self.url = url

        @ttl_cache(ttl=900)
        def __fetch_data(self) -> dict[str, Union[None, str, list[str]]]:
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
                "status": metadata.get("Status"),
                "authors": metadata.get("Author"),
                "created": metadata.get("Created"),
                "discussion": metadata.get("Discussion"),
                "description": soup.find("big", {"class": "rpip-description"}).text
            }

        def __getattr__(self, item):
            try:
                return self.__fetch_data()[item] or "N/A"
            except KeyError:
                raise AttributeError(f"RPIP has no attribute '{item}'")

    @rpip.autocomplete("name")
    async def get_rpip_names(self, ctx: Context, current: str):
        names = self.get_rpips().keys()
        return [Choice(name=name, value=name) for name in names if current.lower() in name.lower()][:-26:-1]

    @ttl_cache(ttl=300)
    def get_rpips(self) -> dict[str, 'RPIPS.RPIP']:
        html_doc = requests.get("https://rpips.rocketpool.net/all").text
        soup = BeautifulSoup(html_doc, "html.parser")
        rpips: dict[str, 'RPIPS.RPIP'] = {}

        for row in soup.table.find_all("tr", recursive=False):
            rpip_num = int(row.find("td", {"class": "rpipnum"}).text)
            url = f"https://rpips.rocketpool.net/RPIPs/RPIP-{rpip_num}"
            title = row.find("td", {"class": "title"}).text.strip()
            rpips[f"RPIP-{rpip_num}: {title}"] = self.RPIP(url)

        return rpips


async def setup(bot):
    await bot.add_cog(RPIPS(bot))
