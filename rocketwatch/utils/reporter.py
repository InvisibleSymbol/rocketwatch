import asyncio
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

async def wait_and_execute(func, *args, delay=5, **kwargs):
    await asyncio.sleep(delay)
    await func(*args, **kwargs)

async def report_error(excep, *args, ctx=None, retry_count=0, max_retries=5, delay=5):
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

    try:
        with io.StringIO(details) as f:
            await channel.send(desc, file=File(fp=f, filename="exception.txt"))
    except Exception as e:
        if retry_count < max_retries:
            log.warning(f"Failed to send message. Retrying in {delay} seconds. {e}")
            asyncio.create_task(wait_and_execute(report_error, excep, *args, ctx=ctx, retry_count=retry_count+1, delay=delay))
        else:
            log.error(f"Failed to send message. Max retries reached. {e}")

