import logging
import math
import os

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
    try:
      return await ctx.send('An unexpected error occurred. This Error has been automatically reported.', hidden=True)
    except discord.errors.NotFound:
      pass


log.info(f"Loading Plugins")

for filename in os.listdir("./plugins"):
  if not filename.endswith(".py") or filename.startswith("lib"):
    continue
  filename = filename.split(".")[0]
  log.debug(f"Loading Plugin \"{filename}\"")
  bot.load_extension(f"plugins.{filename}")

log.info(f"Finished loading Plugins")

log.info(f"Starting bot")
bot.run(os.getenv("DISCORD_KEY"))
