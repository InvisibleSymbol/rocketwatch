import logging
import math
from pathlib import Path

import discord.errors
from discord.ext import commands

from utils import reporter
from utils.cfg import cfg
from utils.visibility import is_hidden

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s:%(lineno)d|%(funcName)s(): %(message)s")
log = logging.getLogger("discord_bot")
log.setLevel(cfg["log_level"])

"""
bot = commands.Bot(command_prefix=';',
                   self_bot=True,
                   help_command=None,
                   intents=Intents.default())
"""
bot = discord.Bot()
reporter.bot = bot


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
        await reporter.report_error(excep, ctx=ctx)
        msg = f'{ctx.author.mention} An unexpected error occurred. This Error has been automatically reported.'
        try:
            # try to inform the user. this might fail if it took too long to respond
            return await ctx.respond(msg, ephemeral=is_hidden(ctx))
        except discord.errors.NotFound:
            # so fall back to a normal channel message if that happens
            return await ctx.channel.send(msg)


# attach to ready event
@bot.event
async def on_ready():
    log.info(f'Logged in as {bot.user.name} ({bot.user.id})')


log.info(f"Running using Storage Contract {cfg['rocketpool.manual_addresses.rocketStorage']} (Chain: {cfg['rocketpool.chain']})")
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
