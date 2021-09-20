import io
import logging
import os
import traceback

from discord import File

log = logging.getLogger("reporter")


def format_stacktrace(error):
  return "".join(traceback.format_exception(type(error), error, error.__traceback__))


async def report_error(ctx, excep):
  desc = f"```{excep}\n" \
         f"{ctx.command=}\n" \
         f"{ctx.args=}\n" \
         f"{ctx.channel=}\n" \
         f"{ctx.author=}```"
  log.error(desc)

  if hasattr(excep, "original"):
    details = format_stacktrace(excep.original)
  else:
    details = format_stacktrace(excep)
  log.error(details)

  channel = await ctx.bot.fetch_channel(os.getenv("OWNER_CHANNEL_ERRORS"))
  with io.StringIO(details) as f:
    await channel.send(desc, file=File(fp=f, filename="exception.txt"))
