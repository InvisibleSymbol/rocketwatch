import io
import logging

from discord import app_commands, Interaction, Message, ui, TextStyle, AllowedMentions, ButtonStyle, File
from discord.ext.commands import Cog, GroupCog
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import Embed

log = logging.getLogger("support-threads")
log.setLevel(cfg["log_level"])


async def generate_boiler_embed(db):
    # get the boiler message from the database
    boiler = await db.support_bot.find_one({'_id': 'boiler'})
    # generate the embed
    return Embed(title=boiler['title'], description=boiler['description'])


# Define a simple View that gives us a counter button
class AdminView(ui.View):
    def __init__(self, db: AsyncIOMotorClient):
        super().__init__()
        self.db = db

    @ui.button(label='Edit', style=ButtonStyle.blurple)
    async def edit(self, interaction: Interaction, button: ui.Button):
        boiler = await self.db.support_bot.find_one({'_id': 'boiler'})
        # Make sure to update the message with our update
        await interaction.response.send_modal(AdminModal(boiler["title"], boiler["description"], self.db))


class AdminModal(ui.Modal,
                 title="Change Boiler Message"):
    def __init__(self, old_title, old_description, db):
        super().__init__()
        self.db = db
        self.title_field = ui.TextInput(
            label="Title",
            placeholder="Enter a title",
            default=old_title)
        self.description_field = ui.TextInput(
            label="Description",
            placeholder="Enter a description",
            default=old_description,
            style=TextStyle.paragraph,
            max_length=4000)
        self.add_item(self.title_field)
        self.add_item(self.description_field)

    async def on_submit(self, interaction: Interaction) -> None:
        # export data from modal
        data = interaction.data["components"]
        title = data[0]["components"][0]["value"]
        description = data[1]["components"][0]["value"]
        # get the data from the db
        boiler = await self.db.support_bot.find_one({'_id': 'boiler'})
        # verify that no changes were made while we were editing
        if self.title_field.value != boiler['title'] or self.description_field.value != boiler['description']:
            # dump the description into a memory file
            with io.StringIO(description) as f:
                await interaction.response.edit_message(
                    embed=Embed(
                        description="Someone made changes while you were editing. Please try again.\n"
                                    "Your pending changes have been attached to this message."), view=None)
                a = await interaction.original_response()
                await a.add_files(File(fp=f, filename="pending_description_dump.txt"))
            return
        await self.db.support_bot.update_one({"_id": "boiler"}, {"$set": {"title": title, "description": description}})
        embeds = [Embed(), await generate_boiler_embed(self.db)]
        embeds[0].title = "Support Admin UI"
        embeds[0].description = "The following is a preview of what will be posted in new threads.\n" \
                                "Edit it using the 'Edit' Button."
        await interaction.response.edit_message(embeds=embeds, view=AdminView(self.db))


class SupportUtils(GroupCog, name="support"):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.ctx_menu = app_commands.ContextMenu(
            name='New Support Thread',
            callback=self.my_cool_context_menu,
        )
        self.bot.tree.add_command(self.ctx_menu)

    @Cog.listener()
    async def on_ready(self):
        # insert the boiler message into the database, if it doesn't already exist
        await self.db.support_bot.update_one(
            {'_id': 'boiler'},
            {'$setOnInsert': {
                'title'      : 'Automated Support Message',
                'description': 'This is an automated support message.'
            }},
            upsert=True
        )

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    @app_commands.command()
    @app_commands.guilds(cfg["rocketpool.support.server_id"])
    async def admin_ui(self, interaction: Interaction):
        if cfg["rocketpool.support.role_id"] not in [r.id for r in interaction.user.roles]:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # send 2 embeds, one indicating what the command does, and one showing the current boiler message
        embeds = [Embed(), await generate_boiler_embed(self.db)]
        embeds[0].title = "Support Admin UI"
        embeds[0].description = "The following is a preview of what will be posted in new threads.\n" \
                                "Edit it using the 'Edit' Button."
        await interaction.edit_original_response(embeds=embeds, view=AdminView(self.db))

    # You can add checks too
    @app_commands.guilds(cfg["rocketpool.support.server_id"])
    async def my_cool_context_menu(self, interaction: Interaction, message: Message):
        if cfg["rocketpool.support.role_id"] not in [r.id for r in interaction.user.roles]:
            await interaction.response.send_message(
                embed=Embed(title="Error", description="You do not have permission to use this command."), ephemeral=True)
            return
        if interaction.channel.id != cfg["rocketpool.support.channel_id"]:
            await interaction.response.send_message(
                embed=Embed(title="Error", description="This command can only be used in the support channel."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        author = message.author
        initiator = interaction.user
        try:
            a = await message.create_thread(name=f"{author} - Automated Support Thread",
                                            reason=f"Automated Support Thread ({author}): triggered by {initiator}",
                                            auto_archive_duration=60)

            await a.send(
                content=f"{author.mention}, {initiator.mention}",
                embed=await generate_boiler_embed(self.db),
                allowed_mentions=AllowedMentions(users=True))
            await interaction.edit_original_response(
                embed=Embed(
                    title="Support Thread Successfully Created",
                ),
            )
        except Exception as e:
            await interaction.edit_original_response(
                embed=Embed(
                    title="Error",
                    description=f"{e}"
                ),
            )
            raise e


async def setup(bot):
    await bot.add_cog(SupportUtils(bot))
