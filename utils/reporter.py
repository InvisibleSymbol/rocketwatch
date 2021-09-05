import io
import os
import traceback

from discord import File


def format_stacktrace(error):
  return "".join(traceback.format_exception(type(error), error, error.__traceback__))


async def report_error(ctx, excep):
  desc = f"`{excep}`"
  desc += f"\n```{str(ctx.message.author)=}\n{ctx.message.content=}\n{ctx.message.author.id=}\n{ctx.channel.id=}\n{ctx.guild.id=}```"

  f = io.StringIO()
  if hasattr(excep, "original"):
    f.write(format_stacktrace(excep.original))
  else:
    f.write(format_stacktrace(excep))
  f.seek(0)

  channel = await ctx.bot.fetch_channel(os.getenv("ERROR_CHANNEL"))
  await channel.send(desc, file=File(fp=f, filename="exception.txt"))
  f.close()
