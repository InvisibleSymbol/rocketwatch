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
    async def latest_release(self, ctx: Context):
        """
        Get the latest release of Smart Node for Mainnet.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))

        async with aiohttp.ClientSession() as session:
            res = await session.get("https://api.github.com/repos/rocket-pool/smartnode-install/tags")
            res = await res.json()
        latest_release = f"[{res[0]['name']}]({self.tag_url + res[0]['name']})"
        e = Embed()
        e.add_field(name="Latest Smart Node Release", value=latest_release, inline=False)

        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Releases(bot))
