from discord.ext import commands

from strings import _
from utils.slash_permissions import owner_only_slash


class Reloader(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

  @owner_only_slash()
  async def load(self, ctx, module: str):
    """Loads a module."""
    try:
      self.bot.load_extension("plugins." + module)
      await ctx.send(_("reloader.load", name=module), hidden=True)
    except commands.errors.ExtensionAlreadyLoaded:
      await ctx.send(_("reloader.already_loaded", name=module), hidden=True)
    except commands.errors.ExtensionNotFound:
      await ctx.send(_("reloader.not_found", name=module), hidden=True)

  @owner_only_slash()
  async def unload(self, ctx, module: str):
    """Unloads a module."""
    if module == "reloader":
      await ctx.send(_("reloader.unload_reloader", name=module), hidden=True)
    try:
      self.bot.unload_extension("plugins." + module)
      await ctx.send(_("reloader.unload", name=module), hidden=True)
    except commands.errors.ExtensionNotLoaded:
      await ctx.send(_("reloader.not_loaded", name=module), hidden=True)

  @owner_only_slash()
  async def reload(self, ctx, module: str):
    """Reloads a module."""
    try:
      self.bot.reload_extension("plugins." + module)
      await ctx.send(_("reloader.reload", name=module), hidden=True)
    except commands.errors.ExtensionNotLoaded:
      await ctx.send(_("reloader.not_loaded", name=module), hidden=True)


def setup(bot):
  bot.add_cog(Reloader(bot))
