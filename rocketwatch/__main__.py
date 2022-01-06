import logging
from pathlib import Path

import discord.errors

from utils import reporter
from utils.cfg import cfg

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s:%(lineno)d|%(funcName)s(): %(message)s")
log = logging.getLogger("discord_bot")
log.setLevel(cfg["log_level"])
logging.getLogger().setLevel("INFO")
logging.getLogger("discord.client").setLevel(cfg["log_level"])

intents = discord.Intents.none()
intents.guilds = True
bot = discord.Bot(intents=intents)
reporter.bot = bot

log.info(f"Running using Storage Contract {cfg['rocketpool.manual_addresses.rocketStorage']} "
         f"(Chain: {cfg['rocketpool.chain']})")
log.info('Loading Plugins')

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

log.info('Finished loading Plugins')

log.info('Starting bot')
bot.run(cfg["discord.secret"])
