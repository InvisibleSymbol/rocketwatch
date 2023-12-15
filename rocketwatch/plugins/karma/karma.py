import logging

from discord import app_commands, Interaction, User, AppCommandType
from discord.app_commands.checks import cooldown
from discord.ext.commands import Cog, GroupCog
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel

from utils.cfg import cfg

log = logging.getLogger("karma")
log.setLevel(cfg["log_level"])


class KaramaUtils(GroupCog):

    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.menus = []
        for i in range(2):
            c = 10 ** i
            self.menus.append(app_commands.ContextMenu(
                name=f"Give {c} Point{'s' if c != 1 else ''}",
                callback=self.add_user_points,
                type=AppCommandType.user,
                guild_ids=[cfg["rocketpool.support.server_id"]],
                extras={"amount": c}
            ))
            self.menus.append(app_commands.ContextMenu(
                name=f"Remove {c} Point{'s' if c != 1 else ''}",
                callback=self.remove_user_points,
                type=AppCommandType.user,
                guild_ids=[cfg["rocketpool.support.server_id"]],
                extras={"amount": c}
            ))

        for menu in self.menus:
            self.bot.tree.add_command(menu)

    @Cog.listener()
    async def on_ready(self):
        # ensure user and issuer indexes exist
        await self.db.karma.create_indexes([
            IndexModel("user"),
            IndexModel("issuer")
        ])

    async def cog_unload(self) -> None:
        for menu in self.menus:
            self.bot.tree.remove_command(menu)

    @app_commands.guilds(cfg["rocketpool.support.server_id"])
    @cooldown(1, 10)
    async def add_user_points(self, interaction: Interaction, user: User):
        await interaction.response.defer(ephemeral=True)
        # dissallow users from giving themselves points
        if user.id == interaction.user.id:
            await interaction.edit_original_response(
                content="You can't give yourself points!",
            )
            return
        amount = interaction.command.extras["amount"]
        await self.db.karma.update_one(
            {"user": user.id, "issuer": interaction.user.id},
            {"$inc": {"points": amount}},
            upsert=True
        )
        # create a self-deleting announcement message
        await interaction.channel.send(
            f"Gave {amount} `{interaction.user.global_name}` point{'s' if amount != 1 else ''} to `{user.global_name}`!",
            delete_after=30
        )
        await interaction.delete_original_response()

    @app_commands.guilds(cfg["rocketpool.support.server_id"])
    @cooldown(1, 10)
    async def remove_user_points(self, interaction: Interaction, user: User):
        await interaction.response.defer(ephemeral=True)
        # dissallow users from giving themselves points
        if user.id == interaction.user.id:
            await interaction.edit_original_response(
                content="You can't remove points from yourself!",
            )
            return
        amount = interaction.command.extras["amount"]
        await self.db.karma.update_one(
            {"user": user.id, "issuer": interaction.user.id},
            {"$inc": {"points": -amount}},
            upsert=True
        )
        # create a self-deleting announcement message
        await interaction.channel.send(
            f"Removed {amount} `{interaction.user.global_name}` point{'s' if amount != 1 else ''} from `{user.global_name}`!",
            delete_after=30
        )
        await interaction.delete_original_response()


async def setup(self):
    await self.add_cog(KaramaUtils(self))
