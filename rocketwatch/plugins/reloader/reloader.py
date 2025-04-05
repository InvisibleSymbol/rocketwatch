from discord import Interaction
from discord.app_commands import command, guilds, autocomplete, Choice
from discord.ext.commands import Cog
from discord.ext.commands import (
    is_owner,
    ExtensionNotLoaded,
    ExtensionAlreadyLoaded,
    ExtensionNotFound
)
from pathlib import Path

from rocketwatch import RocketWatch
from utils.cfg import cfg


class Reloader(Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        
    async def _get_loaded_extensions(self, interaction: Interaction, current: str) -> list[Choice[str]]:
        loaded = {ext.split(".")[-1] for ext in self.bot.extensions.keys()}
        return [Choice(name=plugin, value=plugin) for plugin in loaded if current.lower() in plugin.lower()][:25]

    async def _get_unloaded_extensions(self, interaction: Interaction, current: str) -> list[Choice[str]]:
        loaded = {ext.split(".")[-1] for ext in self.bot.extensions.keys()}
        all = {path.stem for path in Path("plugins").glob('**/*.py')}
        return [Choice(name=plugin, value=plugin) for plugin in (all - loaded) if current.lower() in plugin.lower()][:25]

    @command()
    @guilds(cfg["discord.owner.server_id"])
    @is_owner()
    @autocomplete(module=_get_unloaded_extensions)
    async def load(self, interaction: Interaction, module: str):
        """Load a new module"""
        await interaction.response.defer()
        try:
            await self.bot.load_extension(f"plugins.{module}.{module}")
            await interaction.followup.send(content=f"Loaded plugin `{module}`!")
            await self.bot.sync_commands()
        except ExtensionAlreadyLoaded:
            await interaction.followup.send(content=f"Plugin `{module}` already loaded!")
        except ExtensionNotFound:
            await interaction.followup.send(content=f"Plugin `{module}` not found!")
            
    @command()
    @guilds(cfg["discord.owner.server_id"])
    @is_owner()
    @autocomplete(module=_get_loaded_extensions)
    async def unload(self, interaction: Interaction, module: str):
        """Unload a module"""
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.unload_extension(f"plugins.{module}.{module}")
            await interaction.followup.send(content=f"Unloaded plugin `{module}`!")
            await self.bot.sync_commands()
        except ExtensionNotLoaded:
            await interaction.followup.send(content=f"Plugin `{module}` not loaded!")

    @command()
    @guilds(cfg["discord.owner.server_id"])
    @is_owner()
    @autocomplete(module=_get_loaded_extensions)
    async def reload(self, interaction: Interaction, module: str):
        """Reload a module"""
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.reload_extension(f"plugins.{module}.{module}")
            await interaction.followup.send(content=f"Reloaded plugin `{module}`!")
            await self.bot.sync_commands()
        except ExtensionNotLoaded:
            await interaction.followup.send(content=f"Plugin {module} not loaded!")
    

async def setup(bot):
    await bot.add_cog(Reloader(bot))
