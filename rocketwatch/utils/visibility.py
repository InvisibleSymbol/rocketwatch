from discord.ext.commands import Context


def is_hidden(ctx: Context):
    return any(w in ctx.channel.name for w in ["random", "rocket-watch"])


def is_hidden_weak(ctx: Context):
    return any(w in ctx.channel.name for w in ["random", "rocket-watch", "trading"])
