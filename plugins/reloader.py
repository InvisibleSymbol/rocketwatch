from discord.ext import commands

from strings import _


class Reloader(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

  @commands.command(hidden=True)
  @commands.is_owner()
  async def load(self, ctx, module: str):
    """Loads a module."""
    try:
      self.bot.load_extension("plugins." + module)
      await ctx.channel.send(_("reloader.load", name=module))
    except commands.errors.ExtensionAlreadyLoaded:
      await ctx.channel.send(_("reloader.already_loaded", name=module))
    except commands.errors.ExtensionNotFound:
      await ctx.channel.send(_("reloader.not_found", name=module))

  @commands.command(hidden=True)
  @commands.is_owner()
  async def unload(self, ctx, module: str):
    """Unloads a module."""
    if module == "reloader":
      await ctx.channel.send(_("reloader.unload_reloader", name=module))
      return
    try:
      self.bot.unload_extension("plugins." + module)
      await ctx.channel.send(_("reloader.unload", name=module))
    except commands.errors.ExtensionNotLoaded:
      await ctx.channel.send(_("reloader.not_loaded", name=module))

  @commands.command(hidden=True)
  @commands.is_owner()
  async def reload(self, ctx, module: str):
    """Reloads a module."""
    try:
      self.bot.reload_extension("plugins." + module)
      await ctx.send(_("reloader.reload", name=module), hidden=True)
    except commands.errors.ExtensionNotLoaded:
      await ctx.send(_("reloader.not_loaded", name=module), hidden=True)
    return True


def setup(bot):
  bot.add_cog(Reloader(bot))
