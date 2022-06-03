from discord.ext.commands import Context


def is_hidden(ctx: Context):
    return ctx.channel.name not in ["random", "rocket-watch"]
