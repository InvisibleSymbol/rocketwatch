import io
import logging
import traceback

from discord import File

from utils.cfg import cfg
from utils.get_or_fetch import get_or_fetch_channel

log = logging.getLogger("reporter")
log.setLevel(cfg["log_level"])
bot = None


def format_stacktrace(error):
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


async def report_error(excep, *args, ctx=None):
    desc = f"**`{repr(excep)[:100]}`**\n"
    if args:
        desc += "```"
        desc += "\n".join(f"args[{i}]={arg}" for i, arg in enumerate(args))
        desc += "```\n"
    if ctx:
        desc += f"```{ctx.command.name=}\n" \
                f"{ctx.command.params=}\n" \
                f"{ctx.channel=}\n" \
                f"{ctx.author=}```"

    if hasattr(excep, "original"):
        details = format_stacktrace(excep.original)
    else:
        details = format_stacktrace(excep)
    log.error(details)
    if not bot:
        log.warning("cant send error as bot variable not initialized")
    channel = await get_or_fetch_channel(bot, cfg["discord.channels.errors"])
    with io.StringIO(details) as f:
        await channel.send(desc, file=File(fp=f, filename="exception.txt"))
