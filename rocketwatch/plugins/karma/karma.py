import logging

from discord import app_commands, Interaction, User, AppCommandType
from discord.app_commands.checks import cooldown
from discord.ext.commands import Cog, GroupCog
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden

log = logging.getLogger("karma")
log.setLevel(cfg["log_level"])


class KarmaUtils(GroupCog, name="karma"):
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
            f"Gave {amount} `{interaction.user.global_name or interaction.user.name}`"
            f" point{'s' if amount != 1 else ''} to `{user.global_name or user.name}`!",
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
            f"Removed {amount} `{interaction.user.global_name or interaction.user.name}`"
            f" point{'s' if amount != 1 else ''} to `{user.global_name or user.name}`!",
            delete_after=30
        )
        await interaction.delete_original_response()

    @app_commands.command(name="top")
    async def top(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=is_hidden(interaction))
        # find the top karma users
        top = await self.db.karma.aggregate([
            {"$group": {"_id": "$user", "points": {"$sum": "$points"}}},
            {"$sort": {"points": -1}},
            {"$limit": 10},
            # lookup top issuer for each user
            {"$lookup": {
                "from"        : "karma",
                "localField"  : "_id",
                "foreignField": "user",
                "as"          : "issuer"
            }},
            {"$unwind": "$issuer"},
            {"$project": {"_id": 1, "points": 1, "issuer": "$issuer.issuer"}}

        ]).to_list(length=10)
        e = Embed(title="Top 10 Karma Users")
        des = ""
        for i, u in enumerate(top):
            # try to resolve users
            user = self.bot.get_user(u["_id"])
            if not user:
                user = await self.bot.fetch_user(u["_id"])
            issuer = self.bot.get_user(u["issuer"])
            if not issuer:
                issuer = await self.bot.fetch_user(u["issuer"])
            des += f"`{f'#{str(i + 1)}':>3}` {user.mention} – `{u['points']}` points (most given by {issuer.mention})\n"

        e.description = des
        await interaction.edit_original_response(embed=e)

    # user lookup command, defaults to caller. top 10 points split by issuer
    @app_commands.command(name="user")
    async def user(self, interaction: Interaction, user: User = None):
        await interaction.response.defer(ephemeral=is_hidden(interaction) or not user)
        if not user:
            user = interaction.user
        # find the top karma users
        top = await self.db.karma.find({"user": user.id}).sort("points", -1).to_list(length=10)
        if not top:
            await interaction.edit_original_response(content=f"`{user.mention}` has no points!")
            return
        # fetch total score for user
        total = await self.db.karma.aggregate([
            {"$match": {"user": user.id}},
            {"$group": {"_id": "$user", "points": {"$sum": "$points"}}}
        ]).to_list(length=1)
        e = Embed(title=f"Points held by {user.global_name or user.name}")
        des = ""
        if total:
            des += f"**Total points: `{total[0]['points']}`**\n"
        for u in top:
            issuer = self.bot.get_user(u["issuer"]) or await self.bot.fetch_user(u["issuer"])
            des += f"– `{u['points']}` points received from {issuer.mention}\n"
        e.description = des
        await interaction.edit_original_response(embed=e)


async def setup(self):
    await self.add_cog(KarmaUtils(self))
