import discord
from discord.commands import permissions

from utils.cfg import cfg

bot = discord.Bot()

guilds = cfg["discord.guilds"]


def owner_only_slash():
    return bot.slash_command(default_permission=False,
                             guild_ids=[cfg["discord.owner.server_id"]],
                             permissions=[permissions.Permission(id=cfg["discord.owner.user_id"],
                                                                 type=2,
                                                                 permission=True,
                                                                 guild_id=cfg["discord.owner.server_id"])])
