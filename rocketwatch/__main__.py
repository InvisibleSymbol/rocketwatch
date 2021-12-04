import logging
import math
from pathlib import Path

import discord.errors
from discord.ext import commands
from discord.errors import NotFound

from utils import reporter
from utils.cfg import cfg
from utils.visibility import is_hidden

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s:%(lineno)d|%(funcName)s(): %(message)s")
log = logging.getLogger("discord_bot")
log.setLevel(cfg["log_level"])

bot = discord.Bot()
reporter.bot = bot


@bot.event
async def on_application_command_error(ctx, excep):
    await reporter.report_error(excep, ctx=ctx)
    msg = f'{ctx.author.mention} An unexpected error occurred. This Error has been automatically reported.'
    try:
        # try to inform the user. this might fail if it took too long to respond
        return await ctx.respond(msg, ephemeral=is_hidden(ctx))
    except NotFound:
        # so fall back to a normal channel message if that happens
        return await ctx.channel.send(msg)


# attach to ready event
@bot.event
async def on_ready():
    log.info(f'Logged in as {bot.user.name} ({bot.user.id})')


log.info(f"Running using Storage Contract {cfg['rocketpool.manual_addresses.rocketStorage']} "
         f"(Chain: {cfg['rocketpool.chain']})")
log.info(f"Loading Plugins")

for path in Path("plugins").glob('**/*.py'):
    plugin_name = path.parts[1]
    if path.stem != plugin_name:
        log.warning(f"Skipping plugin {plugin_name}")
        continue
    extension_name = f"plugins.{plugin_name}.{plugin_name}"
    log.debug(f"Loading Plugin \"{extension_name}\"")
    try:
        bot.load_extension(extension_name)
    except Exception as err:
        log.error(f"Failed to load plugin \"{extension_name}\"")
        log.exception(err)

log.info(f"Finished loading Plugins")

log.info(f"Starting bot")
bot.run(cfg["discord.secret"])
