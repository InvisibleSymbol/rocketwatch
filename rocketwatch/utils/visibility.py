from discord.ext.commands import Context

from plugins.support_utils.support_utils import has_perms


def is_hidden(ctx: Context):
    return all(w not in ctx.channel.name for w in ["random", "rocket-watch"])


def is_hidden_weak(ctx: Context):
    return all(w not in ctx.channel.name for w in ["random", "rocket-watch", "trading"])


def is_hidden_role_controlled(ctx: Context):
    # reuses the has_perms function from support_utils, but overrides it when is_hidden would return false
    return not has_perms(ctx.interaction, "") if is_hidden(ctx) else False
