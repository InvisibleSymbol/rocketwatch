from discord.ext import commands

from utils.slash_permissions import owner_only_slash


class Reloader(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @owner_only_slash()
    async def load(self, ctx, module: str):
        """Loads a module."""
        try:
            self.bot.load_extension(f"plugins.{module}.plugin")
            await ctx.send(f"Loaded {module} Plugin!", hidden=True)
        except commands.errors.ExtensionAlreadyLoaded:
            await ctx.send(f"Plugin {module} already loaded!", hidden=True)
        except commands.errors.ExtensionNotFound:
            await ctx.send(f"Plugin {module} not found!", hidden=True)

    @owner_only_slash()
    async def unload(self, ctx, module: str):
        """Unloads a module."""
        try:
            self.bot.unload_extension(f"plugins.{module}.plugin")
            await ctx.send(f"Unloaded {module} Plugin!", hidden=True)
        except commands.errors.ExtensionNotLoaded:
            await ctx.send(f"Plugin {module} not loaded!", hidden=True)

    @owner_only_slash()
    async def reload(self, ctx, module: str):
        """Reloads a module."""
        try:
            self.bot.reload_extension(f"plugins.{module}.plugin")
            await ctx.send(f"Reloaded {module} Plugin!", hidden=True)
        except commands.errors.ExtensionNotLoaded:
            await ctx.send(f"Plugin {module} not loaded!", hidden=True)


def setup(bot):
    bot.add_cog(Reloader(bot))
