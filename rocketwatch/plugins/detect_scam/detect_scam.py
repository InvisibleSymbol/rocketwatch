import io
import asyncio
import logging
import contextlib
import regex as re

from urllib import parse
from typing import Optional
from datetime import datetime, timezone

from cachetools import TTLCache
from discord import (
    errors,
    app_commands,
    File,
    Color,
    User,
    Message,
    Reaction,
    Guild,
    DeletedReferencedMessage,
    Interaction, RawMessageDeleteEvent
)
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed


log = logging.getLogger("detect_scam")
log.setLevel(cfg["log_level"])


def get_text_of_message(message: Message) -> str:
    text = ""
    if message.content:
        text += message.content.replace("\n", "") + "\n"
    if message.embeds:
        for embed in message.embeds:
            text += f"---\n Embed: {embed.title}\n{embed.description}\n---\n"
    return parse.unquote(text).lower()


class DetectScam(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb.uri"]).get_database("rocketwatch")
        self.report_lock = asyncio.Lock()
        self.reaction_lock = asyncio.Lock()
        self.message_react_cache = TTLCache(maxsize=1000, ttl=300)
        self.__markdown_link_pattern = re.compile(r"(?<=\[)([^/\] ]*).+?(?<=\(https?:\/\/)([^/\)]*)")
        self.__basic_url_pattern = re.compile(r"https?:\/\/([/\\@\-_0-9a-zA-Z]+\.)+[\\@\-_0-9a-zA-Z]+")
        self.__invite_pattern = re.compile(r"((discord(app)?\.com\/invite)|(dsc\.gg))(\\|\/)(?P<code>[a-zA-Z0-9]+)")
        self.report_menu = app_commands.ContextMenu(
            name="Report as Spam",
            callback=self.manual_report,
            guild_ids=[cfg["rocketpool.support.server_id"]]
        )
        self.bot.tree.add_command(self.report_menu)

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.report_menu.name, type=self.report_menu.type)

    async def _get_report_components(self, message: Message, reason: str) -> Optional[tuple[Embed, Embed, File]]:
        try:
            message = await message.channel.fetch_message(message.id)
            if isinstance(message, DeletedReferencedMessage):
                return None
        except errors.NotFound:
            return None

        async with self.report_lock:
            if await self.db.scam_reports.find_one({"guild_id": message.guild.id, "message_id": message.id}):
                log.info(f"Found existing report for message {message.id} in database")
                return None

            warning = Embed(title="🚨 Warning: Possible Scam Detected")
            warning.color = Color.from_rgb(255, 0, 0)
            warning.description = f"**Reason:** {reason}\n"

            report = warning.copy()
            warning.set_footer(text="This message will be deleted once the suspicious message is removed.")

            report.description += f"User ID: `{message.author.id}` ({message.author.mention})\nMessage ID: `{message.id}` ({message.jump_url})\nChannel ID: `{message.channel.id}` ({message.channel.mention})\n\n"
            report.description += "Original message has been attached as a file. Please review and take appropriate action."

            text = get_text_of_message(message)
            with io.BytesIO(text.encode()) as f:
                contents = File(f, filename="original_message.txt")

            await self.db.scam_reports.insert_one({
                "guild_id": message.guild.id,
                "channel_id": message.channel.id,
                "message_id": message.id,
                "user_id": message.author.id,
                "reason": reason,
                "content": text,
                "warning_id": None,
                "report_id": None,
            })
            return warning, report, contents

    async def report_suspicious_message(self, message: Message, reason: str) -> None:
        if not (components := await self._get_report_components(message, reason)):
            return None

        warning, report, contents = components
        with contextlib.suppress(errors.Forbidden):
            warning_msg = await message.reply(embed=warning, mention_author=False)

        report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])
        report_msg = await report_channel.send(embed=report, file=contents)

        await self.db.scam_reports.update_one(
            {"guild_id": message.guild.id, "message_id": message.id},
            {"$set": {"warning_id": warning_msg.id, "report_id": report_msg.id}}
        )
        return None

    async def manual_report(self, interaction: Interaction, message: Message) -> None:
        await interaction.response.defer(ephemeral=True)

        if message.author == interaction.user:
            await interaction.followup.send(content="You can't report yourself!")
            return None

        reporter = await self.bot.get_or_fetch_user(interaction.user.id)
        reason = f"Manual report by {reporter.mention}"

        if not (components := await self._get_report_components(message, reason)):
            await interaction.followup.send(
                content="Failed to report message. It may have already been reported or deleted.", ephemeral=True
            )
            return None

        try:
            warning, report, contents = components
            report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])
            report_msg = await report_channel.send(embed=report, file=contents)
            moderator = await self.bot.get_or_fetch_user(cfg["rocketpool.support.moderator_id"])
            warning_msg = await message.reply(
                content=f"{moderator.mention} {report_msg.jump_url}",
                embed=warning,
                mention_author=False
            )
            await self.db.scam_reports.update_one(
                {"guild_id": message.guild.id, "message_id": message.id},
                {"$set": {"warning_id": warning_msg.id, "report_id": report_msg.id}}
            )
            await interaction.followup.send(content="Thank you for reporting!", ephemeral=True)
        except Exception as e:
            await self.bot.report_error(e)
            await interaction.followup.send(
                content="Failed to send report details! The error has been reported.", ephemeral=True
            )

        return None

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        # if self, ignore
        if message.author.id == self.bot.user.id:
            return
        if message.guild is None:
            return
        if message.guild.id != cfg["rocketpool.support.server_id"]:
            log.warning(f"Ignoring message from {message.guild.id} (Content: {message.content})")
            return
        checks = [
            self.markdown_link_trick(message),
            self.link_and_keywords(message),
            self.paperhands(message),
            self.mention_everyone(message),
            self.discord_invite(message)
        ]
        await asyncio.gather(*checks)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: Reaction, user: User) -> None:
        if reaction.message.guild.id != cfg["rocketpool.support.server_id"]:
            log.warning(f"Ignoring reaction from {reaction.message.guild.id} (Content: {reaction.message.content})")
            return
        checks = [
            self.reaction_spam(reaction, user)
        ]
        await asyncio.gather(*checks)

    async def markdown_link_trick(self, message: Message) -> None:
        txt = get_text_of_message(message)
        for m in self.__markdown_link_pattern.findall(txt):
            if "." in m[0] and m[0] != m[1]:
                await self.report_suspicious_message(
                    message,
                    "Markdown link with possible domain in visible portion that does not match the actual domain."
                )

    async def discord_invite(self, message: Message) -> None:
        txt = get_text_of_message(message)
        if self.__invite_pattern.search(txt):
            await self.report_suspicious_message(
                message,
                "Invite to external server."
            )

    async def link_and_keywords(self, message: Message) -> None:
        # message contains one of the relevant keyword combinations and a link
        txt = get_text_of_message(message)
        if not self.__basic_url_pattern.search(txt):
            return

        keywords = (
            [
                ("open", "create", "raise", "raisse"),
                "ticket"
            ],
            [
                ("contact", "reach out", "report", [("talk", "speak"), ("to", "with")], "ask"),
                ("admin", "mod")
            ],
            ("support team", "supp0rt", "🎫", "🎟️", "m0d"),
            [
                ("ask", "seek", "request", "contact"),
                ("help", "assistance", "service")
            ],
            [
                ("instant", "live"),
                "chat"
            ]
        )

        def txt_contains(_x: list | tuple | str) -> bool:
            match _x:
                case str():
                    return _x in txt
                case tuple():
                    return any(map(txt_contains, _x))
                case list():
                    return all(map(txt_contains, _x))
            return False

        if txt_contains(keywords):
            await self.report_suspicious_message(message, "There is no ticket system in this server.")

    async def paperhands(self, message: Message) -> None:
        # message contains the word "paperhand" and a link
        txt = get_text_of_message(message)
        # if has http and contains the word paperhand or paperhold
        if (any(x in txt for x in ["paperhand", "paperhold", "pages.dev", "web.app"]) and "http" in txt) or "pages.dev" in txt:
            await self.report_suspicious_message(message, "High chance the linked website is a scam.")

    # contains @here or @everyone but doesn't actually have the permission to do so
    async def mention_everyone(self, message: Message) -> None:
        txt = get_text_of_message(message)
        if ("@here" in txt or "@everyone" in txt) and not message.author.guild_permissions.mention_everyone:
            await self.report_suspicious_message(message, "Mentioned @here or @everyone without permission")


    async def reaction_spam(self, reaction: Reaction, user: User) -> None:
        # reaction spam is when one user reacts to a message with multiple reactions by only themselves and in quick succession
        # this is usually done to make the message stand out

        # check if user is a bot
        if user.bot:
            return

        # check if the reaction is by the same user that created the message
        if reaction.message.author.id != user.id:
            return

        # check if the message is new enough (we ignore any reactions on messages older than 5 minutes from now)
        if reaction.message.created_at.timestamp() - datetime.now(timezone.utc).timestamp() > 300:
            return

        async with self.reaction_lock:
            print("reaction")
            # get all reactions on message
            reactions = self.message_react_cache.get(reaction.message.id, default=None)
            if reactions is None:
                reactions = {}
                for reaction in reaction.message.reactions:
                    reactions |= ({reaction.emoji: {user async for user in reaction.users()}})
                self.message_react_cache[reaction.message.id] = reactions

            # insert reaction into reactions
            if reaction.emoji not in reactions:
                reactions[reaction.emoji] = set()
            reactions[reaction.emoji].add(user)

            # if there are 5 reactions done by the author of the message, report it
            if len([r for r in reactions.values() if user in r and len(r) == 1]) >= 12:
                await self.report_suspicious_message(reaction.message, "Reaction spam by message author")

            # update cache
            self.message_react_cache[reaction.message.id] = reactions

            print("reaction done")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, event: RawMessageDeleteEvent) -> None:
        report = await self.db.scam_reports.find_one({"guild_id": event.guild_id, "message_id": event.message_id})
        if not report:
            return

        # delete warning message
        channel = await self.bot.get_or_fetch_channel(report["channel_id"])
        with contextlib.suppress(errors.NotFound):
            message = await channel.fetch_message(report["warning_id"])
            await message.delete()

        # try to update report message to indicate that the message was deleted
        with contextlib.suppress(errors.NotFound):
            report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])
            message = await report_channel.fetch_message(report["report_id"])
            embed = message.embeds[0]
            embed.description += "\n\n**Original message has been deleted.**"
            # orange
            embed.color = Color.from_rgb(255, 165, 0)
            await message.edit(embed=embed)

        # record in db that message was deleted
        await self.db.scam_reports.update_one(
            {"guild_id": event.guild_id, "message_id": event.message_id},
            {"$set": {"warning_id": None}}
        )

    @commands.Cog.listener()
    # on user ban
    async def on_member_ban(self, guild: Guild, user: User) -> None:
        # delete all warnings, update all reports
        reports = await self.db.scam_reports.find({"guild_id": guild.id, "user_id": user.id}).to_list(None)
        report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])

        for report in reports:
            # delete warning message
            channel = await self.bot.get_or_fetch_channel(report["channel_id"])
            with contextlib.suppress(errors.NotFound):
                message = await channel.fetch_message(report["warning_id"])
                await message.delete()

            # try to update report message to indicate that the message was deleted
            with contextlib.suppress(errors.NotFound):
                message = await report_channel.fetch_message(report["report_id"])
                embed = message.embeds[0]
                embed.description += "\n\n**User has been banned.**"
                # green
                embed.color = Color.from_rgb(0, 255, 0)
                await message.edit(embed=embed)

            # record in db that message was deleted
            await self.db.scam_reports.update_one(
                {"guild_id": guild.id, "message_id": report["message_id"]},
                {"$set": {"warning_id": None}}
            )

async def setup(bot):
    await bot.add_cog(DetectScam(bot))
