import os

from discord_slash import cog_ext
from discord_slash.model import SlashCommandPermissionType
from discord_slash.utils.manage_commands import create_permission

owner_server_id = int(os.getenv("OWNER_SERVER_ID"))
output_server_id = int(os.getenv("OWNER_SERVER_ID"))

owner_only_perms = {
  owner_server_id: [
    create_permission(int(os.getenv("OWNER_USER_ID")),
                      SlashCommandPermissionType.USER,
                      True)
  ]
}


def default_slash():
  return cog_ext.cog_slash(guild_ids=[owner_server_id, output_server_id])


def owner_only_slash():
  return cog_ext.cog_slash(guild_ids=[owner_server_id],
                           default_permission=False,
                           permissions=owner_only_perms)
