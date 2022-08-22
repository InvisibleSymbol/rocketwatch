from discord.ext.commands import Context


def is_hidden(ctx: Context):
    return all(w not in ctx.channel.name for w in ["random", "rocket-watch"])


def is_hidden_weak(ctx: Context):
    return all(w not in ctx.channel.name for w in ["random", "rocket-watch", "trading"])
