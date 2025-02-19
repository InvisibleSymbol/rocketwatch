import logging
import uuid
from pathlib import Path

from discord import Intents
from discord.ext.commands import Bot

from utils import reporter
from utils.cfg import cfg

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s:%(lineno)d|%(funcName)s(): %(message)s")
log = logging.getLogger("discord_bot")
log.setLevel(cfg["log_level"])
logging.getLogger().setLevel("INFO")
logging.getLogger("discord.client").setLevel(cfg["log_level"])


class RocketWatch(Bot):
    async def setup_hook(self) -> None:
        chain = cfg["rocketpool.chain"]
        storage = cfg['rocketpool.manual_addresses.rocketStorage']
        log.info(f"Running using storage contract {storage} (Chain: {chain})")

        log.info('Loading plugins')
        included_modules = set(cfg["modules.include"] or [])
        excluded_modules = set(cfg["modules.exclude"] or [])

        def should_load_plugin(_plugin: str) -> bool:
            # inclusion takes precedence in case of collision
            if _plugin in included_modules:
                log.debug(f"Plugin {_plugin} explicitly included")
                return True
            elif _plugin in excluded_modules:
                log.debug(f"Plugin {_plugin} explicitly excluded")
                return False
            elif len(included_modules) > 0:
                log.debug(f"Plugin {_plugin} implicitly excluded")
                return False
            else:
                log.debug(f"Plugin {_plugin} implicitly included")
                return True

        for path in Path("plugins").glob('**/*.py'):
            plugin_name = path.stem
            if not should_load_plugin(plugin_name):
                log.warning(f"Skipping plugin {plugin_name}")
                continue

            log.debug(f"Loading plugin \"{plugin_name}\"")
            try:
                extension_name = f"plugins.{plugin_name}.{plugin_name}"
                await self.load_extension(extension_name)
            except Exception as err:
                log.error(f"Failed to load plugin \"{plugin_name}\"")
                log.exception(err)

        log.info('Finished loading plugins')


def main() -> None:
    intents = Intents.none()
    intents.guilds = True
    intents.members = True
    intents.messages = True
    intents.message_content = True
    intents.reactions = True
    intents.moderation = True

    prefix = str(uuid.uuid4())
    log.info(f"Using command prefix {prefix}")
    bot = RocketWatch(intents=intents, command_prefix=prefix)
    log.info('Starting bot')
    reporter.bot = bot
    bot.run(cfg["discord.secret"])


if __name__ == '__main__':
    main()
