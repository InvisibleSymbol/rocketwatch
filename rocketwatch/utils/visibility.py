from discord import Interaction
from discord.ext.commands import Context

from plugins.support_utils.support_utils import has_perms


def is_hidden(interaction: Context | Interaction):
    return all(w not in interaction.channel.name for w in ["random", "rocket-watch"])


def is_hidden_weak(interaction: Context | Interaction):
    return all(w not in interaction.channel.name for w in ["random", "rocket-watch", "trading"])


def is_hidden_role_controlled(interaction: Interaction):
    # reuses the has_perms function from support_utils, but overrides it when is_hidden would return false
    return not has_perms(interaction, "") if is_hidden(interaction) else False
