import logging

import aiohttp
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden

log = logging.getLogger("releases")
log.setLevel(cfg["log_level"])


class Releases(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tag_url = "https://github.com/rocket-pool/smartnode-install/releases/tag/"

    @hybrid_command()
    async def latest_releases(self, ctx: Context):
        """
        Get the latest releases of Smart Node for both Mainnet and Prater.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))

        async with aiohttp.ClientSession() as session:
            res = await session.get("https://api.github.com/repos/rocket-pool/smartnode-install/tags")
            res = await res.json()
        latest_prater_release = f"[{res[0]['name']}]({self.tag_url + res[0]['name']})"
        latest_mainnet_release = None
        for tag in res:
            if tag["name"].split(".")[-1].isnumeric():
                latest_mainnet_release = f"[{tag['name']}]({self.tag_url + tag['name']})"
                break
        e = Embed()
        e.add_field(name="Latest Mainnet Release", value=latest_mainnet_release, inline=False)
        e.add_field(name="Latest Prater Release", value=latest_prater_release, inline=False)

        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Releases(bot))
