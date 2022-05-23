from discord import Object
from discord.app_commands import guilds
from discord.ext import commands
from discord.ext.commands import is_owner, ExtensionNotLoaded, ExtensionAlreadyLoaded, ExtensionNotFound, \
    hybrid_command, Context

from utils.cfg import cfg


class Reloader(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # todo add auto complete
    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def load(self, ctx: Context, module: str):
        """Loads a module."""
        await ctx.defer()
        try:
            self.bot.load_extension(f"plugins.{module}.{module}")
            await ctx.send(content=f"Loaded {module} Plugin!")
        except ExtensionAlreadyLoaded:
            await ctx.send(content=f"Plugin {module} already loaded!")
        except ExtensionNotFound:
            await ctx.send(content=f"Plugin {module} not found!")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def unload(self, ctx: Context, module: str):
        """Unloads a module."""
        await ctx.defer()
        try:
            await self.bot.unload_extension(f"plugins.{module}.{module}")
            await ctx.send(content=f"Unloaded {module} Plugin!")
        except ExtensionNotLoaded:
            await ctx.send(content=f"Plugin {module} not loaded!")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def reload(self, ctx: Context, module: str):
        """Reloads a module."""
        await ctx.defer()
        try:
            await self.bot.reload_extension(f"plugins.{module}.{module}")
            await ctx.send(content=f"Reloaded {module} Plugin!")
        except ExtensionNotLoaded:
            await ctx.send(content=f"Plugin {module} not loaded!")


async def setup(bot):
    await bot.add_cog(Reloader(bot))
