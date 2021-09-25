import logging
import math
import os
from pathlib import Path

import discord.errors
from discord import Intents
from discord.ext import commands
from discord_slash import SlashCommand
from dotenv import load_dotenv

from utils.reporter import report_error

load_dotenv()

# https://discord.com/api/oauth2/authorize?client_id=884095717168259142&permissions=0&scope=bot%20applications.commands

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s:%(lineno)d|%(funcName)s(): %(message)s")
log = logging.getLogger("discord_bot")
log.setLevel(os.getenv("LOG_LEVEL"))
logging.getLogger("discord_slash").setLevel(os.getenv("LOG_LEVEL"))

bot = commands.Bot(command_prefix=';',
                   self_bot=True,
                   help_command=None,
                   intents=Intents.default())
slash = SlashCommand(bot,
                     sync_commands=True,
                     sync_on_cog_reload=True)


@bot.event
async def on_slash_command_error(ctx, excep):
  if isinstance(excep, commands.CommandNotFound):
    return

  elif isinstance(excep, commands.CheckFailure):
    try:
      return await ctx.message.add_reaction('\N{NO ENTRY SIGN}')
    except Exception as err:
      log.exception(err)
      return

  elif isinstance(excep, commands.CommandOnCooldown):
    return await ctx.channel.send(
      f'Command is on cooldown, can be used again in '
      f'{math.ceil(excep.retry_after)} seconds',
      delete_after=min(excep.retry_after, 1))

  else:
    await report_error(ctx, excep)
    msg = f'{ctx.author.mention} An unexpected error occurred. This Error has been automatically reported.'
    try:
      # try to inform the user silently. this might fail if it took too long to respond
      return await ctx.send(msg, hidden=True)
    except discord.errors.NotFound:
      # so fall back to a normal channel message
      return await ctx.channel.send(msg)


log.info(f"Loading Plugins")

for path in Path("./plugins").glob('**/plugin.py'):
  extension_name = ".".join(path.parts[:-1] + (path.stem,))
  log.debug(f"Loading Plugin \"{extension_name}\"")
  try:
    bot.load_extension(extension_name)
  except Exception as err:
    log.error(f"Failed to load plugin \"{extension_name}\"")
    log.exception(err)

log.info(f"Finished loading Plugins")

log.info(f"Starting bot")
bot.run(os.getenv("DISCORD_KEY"))
