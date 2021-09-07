import logging
import math
import os

from discord.ext import commands
from dotenv import load_dotenv

from utils.reporter import report_error

load_dotenv()

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s|%(funcName)s(): %(message)s")
log = logging.getLogger("discord_bot")
log.setLevel(os.getenv("LOG_LEVEL"))
bot = commands.Bot(command_prefix=';')


@bot.event
async def on_command_error(ctx, excep):
  log.exception(excep)

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
    return await ctx.channel.send('An unexpected error occurred. This Error has been automatically reported.')


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
