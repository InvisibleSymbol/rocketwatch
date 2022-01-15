import logging

import aiohttp
from discord.commands import slash_command
from discord.ext import commands

from utils.cfg import cfg
from utils.embeds import Embed
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("releases")
log.setLevel(cfg["log_level"])


class Releases(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tag_url = "https://github.com/rocket-pool/smartnode-install/releases/tag/"

    @slash_command(guild_ids=guilds)
    async def latest_releases(self, ctx):
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

        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))


def setup(bot):
    bot.add_cog(Releases(bot))
