from discord_slash import cog_ext
from discord_slash.model import SlashCommandPermissionType
from discord_slash.utils.manage_commands import create_permission

from utils.cfg import cfg

owner_only_perms = {
  cfg["discord.owner.server_id"]: [
    create_permission(cfg["discord.owner.user_id"],
                      SlashCommandPermissionType.USER,
                      True)
  ]
}

guilds = cfg["discord.guilds"]


def owner_only_slash():
  return cog_ext.cog_slash(guild_ids=[cfg["discord.owner.server_id"]],
                           default_permission=False,
                           permissions=owner_only_perms)
