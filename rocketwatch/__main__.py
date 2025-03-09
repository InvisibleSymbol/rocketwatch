import logging
import uuid

from discord import Intents

from utils.cfg import cfg
from rocketwatch import RocketWatch

logging.basicConfig(format="%(levelname)5s %(asctime)s [%(name)s] %(filename)s:%(lineno)d|%(funcName)s(): %(message)s")
logging.getLogger().setLevel("INFO")
logging.getLogger("discord.client").setLevel(cfg["log_level"])

log = logging.getLogger("discord_bot")
log.setLevel(cfg["log_level"])


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
    log.info("Starting bot")
    bot.run(cfg["discord.secret"])


if __name__ == "__main__":
    main()
