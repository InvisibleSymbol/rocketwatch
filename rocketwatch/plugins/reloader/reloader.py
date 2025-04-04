from discord import Object
from discord.app_commands import guilds, Choice
from discord.ext import commands
from discord.ext.commands import (
    is_owner,
    ExtensionNotLoaded,
    ExtensionAlreadyLoaded,
    ExtensionNotFound,
    hybrid_command,
    Context
)
from pathlib import Path

from rocketwatch import RocketWatch
from utils.cfg import cfg


class Reloader(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    # todo add auto complete
    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def load(self, ctx: Context, module: str):
        """Load a new module"""
        await ctx.defer()
        try:
            await self.bot.load_extension(f"plugins.{module}.{module}")
            await ctx.send(content=f"Loaded {module}!")
        except ExtensionAlreadyLoaded:
            await ctx.send(content=f"Plugin {module} already loaded!")
        except ExtensionNotFound:
            await ctx.send(content=f"Plugin {module} not found!")
            
    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def unload(self, ctx: Context, module: str):
        """Unload a module"""
        await ctx.defer()
        try:
            await self.bot.unload_extension(f"plugins.{module}.{module}")
            await ctx.send(content=f"Unloaded {module}!")
        except ExtensionNotLoaded:
            await ctx.send(content=f"Plugin {module} not loaded!")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def reload(self, ctx: Context, module: str):
        """Reload a module"""
        await ctx.defer()
        try:
            await self.bot.reload_extension(f"plugins.{module}.{module}")
            await ctx.send(content=f"Reloaded {module}!")
        except ExtensionNotLoaded:
            await ctx.send(content=f"Plugin {module} not loaded!")
            
    @reload.autocomplete("module")
    @unload.autocomplete("module")
    async def _get_loaded_extensions(self, ctx: Context, current: str) -> list[Choice[str]]:
        loaded = {ext.split(".")[-1] for ext in self.bot.extensions.keys()}
        return [Choice(name=plugin, value=plugin) for plugin in loaded if current.lower() in plugin.lower()][:25]
    
    @load.autocomplete("module")
    async def _get_unloaded_extensions(self, ctx: Context, current: str) -> list[Choice[str]]:
        loaded = {ext.split(".")[-1] for ext in self.bot.extensions.keys()}
        all = {path.stem for path in Path("plugins").glob('**/*.py')}
        return [Choice(name=plugin, value=plugin) for plugin in (all - loaded) if current.lower() in plugin.lower()][:25]


async def setup(bot):
    await bot.add_cog(Reloader(bot))
