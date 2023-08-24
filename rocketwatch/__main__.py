import logging
import uuid
from pathlib import Path

from discord import Intents
from discord.ext import commands
from discord.ext.commands import Bot

from utils import reporter
from utils.cfg import cfg

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s:%(lineno)d|%(funcName)s(): %(message)s")
log = logging.getLogger("discord_bot")
log.setLevel(cfg["log_level"])
logging.getLogger().setLevel("INFO")
logging.getLogger("discord.client").setLevel(cfg["log_level"])


class RocketWatch(Bot):
    async def setup_hook(self):
        log.info(f"Running using Storage Contract {cfg['rocketpool.manual_addresses.rocketStorage']} "
                 f"(Chain: {cfg['rocketpool.chain']})")
        log.info('Loading Plugins')
        for path in Path("plugins").glob('**/*.py'):
            plugin_name = path.parts[1]
            if path.stem != plugin_name or cfg["modules.overwrite"] and plugin_name not in cfg["modules.overwrite"]:
                log.warning(f"Skipping plugin {plugin_name}")
                continue
            extension_name = f"plugins.{plugin_name}.{plugin_name}"
            log.debug(f"Loading Plugin \"{extension_name}\"")
            try:
                await bot.load_extension(extension_name)
            except Exception as err:
                log.error(f"Failed to load plugin \"{extension_name}\"")
                log.exception(err)
        log.info('Finished loading Plugins')


if __name__ == '__main__':
    intents = Intents.none()
    intents.guilds = True
    intents.members = True
    intents.messages = True
    a = str(uuid.uuid4())
    print(f"Using command prefix {a}")
    bot = RocketWatch(intents=intents, command_prefix=a)
    reporter.bot = bot
    log.info('Starting bot')
    bot.run(cfg["discord.secret"])
