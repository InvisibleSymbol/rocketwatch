import io
import asyncio
import logging
import contextlib

import regex as re

from urllib import parse
from typing import Optional
from datetime import datetime, timezone, timedelta

from cachetools import TTLCache
from discord import (
    ui,
    ButtonStyle,
    errors,
    app_commands,
    File,
    Color,
    User,
    Message,
    Reaction,
    Guild,
    Thread,
    DeletedReferencedMessage,
    Interaction,
    RawMessageDeleteEvent,
    RawBulkMessageDeleteEvent,
    RawThreadUpdateEvent,
    RawThreadDeleteEvent
)
from discord.ext.commands import Cog
from motor.motor_asyncio import AsyncIOMotorClient

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed

log = logging.getLogger("detect_scam")
log.setLevel(cfg["log_level"])


class DetectScam(Cog):
    class Color:
        ALERT = Color.from_rgb(255, 0, 0)
        WARN = Color.from_rgb(255, 165, 0)
        OK = Color.from_rgb(0, 255, 0)
        
    class DeletableView(ui.View):
        THRESHOLD = 5
        
        def __init__(self, plugin: 'DetectScam', reportable: Message | Thread):
            super().__init__(timeout=None)
            self.plugin = plugin
            self.reportable = reportable
            self.safu_votes = set()
            
        @staticmethod
        def is_admin(user: User) -> bool:
            return any((
                user.id == cfg["discord.owner.user_id"],
                {role.id for role in user.roles} & set(cfg["rocketpool.support.role_ids"]),
                user.guild_permissions.administrator
            ))
        
        @ui.button(label="Mark Safu", style=ButtonStyle.primary)
        async def mark_safe(self, interaction: Interaction, button: ui.Button) -> None:
            log.info(f"User {interaction.user.id} marked message {interaction.message.id} as safe")
            
            if interaction.user.id in self.safu_votes:
                log.debug(f"User {interaction.user.id} already voted on this message")
                return await interaction.response.send_message(content="You already voted!", ephemeral=True)
                
            if isinstance(self.reportable, Message):
                repr = "message"
                reported_user = self.reportable.author
                db_filter = {"message_id": self.reportable.id}
            else: # Thread
                repr = "thread"
                reported_user = self.reportable.owner
                db_filter = {"channel_id": self.reportable.id, "message_id": None}
                
            if interaction.user == reported_user:
                log.debug(f"User {interaction.user.id} tried to mark their own {repr} as safe")
                return await interaction.response.send_message(
                    content=f"You can't vote on your own {repr}!", 
                    ephemeral=True
                )
        
            self.safu_votes.add(interaction.user.id)
            
            if self.is_admin(interaction.user):
                user_repr = interaction.user.mention
            elif len(self.safu_votes) >= self.THRESHOLD:
                user_repr = "the community"
            else:
                button.label = f"Mark Safu ({len(self.safu_votes)}/{self.THRESHOLD})"
                return await interaction.response.edit_message(view=self)                

            await interaction.message.delete()
            async with self.plugin._update_lock:
                report = await self.plugin.db.scam_reports.find_one(db_filter)
                await self.plugin._update_report(report, f"This has been marked as safe by {user_repr}.")
                await self.plugin.db.scam_reports.update_one(db_filter, {"$set": {"warning_id": None}})
                await interaction.response.send_message(content="Warning removed!", ephemeral=True)

    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb.uri"]).get_database("rocketwatch")
        
        self._report_lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        
        self._message_react_cache = TTLCache(maxsize=1000, ttl=300)
        self.markdown_link_pattern = re.compile(r"(?<=\[)([^/\] ]*).+?(?<=\(https?:\/\/)([^/\)]*)")
        self.basic_url_pattern = re.compile(r"https?:\/\/([/\\@\-_0-9a-zA-Z]+\.)+[\\@\-_0-9a-zA-Z]+")
        self.invite_pattern = re.compile(r"((discord(app)?\.com\/invite)|(dsc\.gg))(\\|\/)(?P<code>[a-zA-Z0-9]+)")

        self.report_command = app_commands.ContextMenu(
            name="Report Message",
            callback=self.manual_report,
            guild_ids=[cfg["rocketpool.support.server_id"]],
        )
        self.bot.tree.add_command(self.report_command)

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.report_command.name, type=self.report_command.type)

    @staticmethod
    def _get_message_content(message: Message) -> str:
        text = ""
        if message.content:
            text += message.content.replace("\n", "") + "\n"
        if message.embeds:
            for embed in message.embeds:
                text += f"---\n Embed: {embed.title}\n{embed.description}\n---\n"
        return parse.unquote(text).lower()

    async def _generate_message_report(self, message: Message, reason: str) -> Optional[tuple[Embed, Embed, File]]:
        try:
            message = await message.channel.fetch_message(message.id)
            if isinstance(message, DeletedReferencedMessage):
                return None
        except errors.NotFound:
            return None

        async with self._report_lock:
            if await self.db.scam_reports.find_one({"message_id": message.id}):
                log.info(f"Found existing report for message {message.id} in database")
                return None

            warning = Embed(title="🚨 Possible Scam Detected")
            warning.color = self.Color.ALERT
            warning.description = f"**Reason**: {reason}\n"

            report = warning.copy()
            warning.set_footer(text="This message will be deleted once the suspicious message is removed.")

            report.description += (
                "\n"
                f"User ID: `{message.author.id}` ({message.author.mention})\n"
                f"Message ID: `{message.id}` ({message.jump_url})\n"
                f"Channel ID: `{message.channel.id}` ({message.channel.jump_url})\n"
                "\n"
                "Original message has been attached as a file.\n"
                "Please review and take appropriate action."
            )

            text = DetectScam._get_message_content(message)
            with io.BytesIO(text.encode()) as f:
                contents = File(f, filename="original_message.txt")

            await self.db.scam_reports.insert_one({
                "guild_id"   : message.guild.id,
                "channel_id" : message.channel.id,
                "message_id" : message.id,
                "user_id"    : message.author.id,
                "reason"     : reason,
                "content"    : text,
                "warning_id" : None,
                "report_id"  : None,
                "user_banned": False,
                "removed"    : False,
            })
            return warning, report, contents

    async def _generate_thread_report(self, thread: Thread, reason: str) -> Optional[tuple[Embed, Embed]]:
        try:
            thread = await thread.guild.fetch_channel(thread.id)
        except (errors.NotFound, errors.Forbidden):
            return None
        
        async with self._report_lock:
            if await self.db.scam_reports.find_one({"channel_id": thread.id, "message_id": None}):
                log.info(f"Found existing report for thread {thread.id} in database")
                return None

            warning = Embed(title="🚨 Possible Scam Detected")
            warning.color = self.Color.ALERT
            warning.description = f"**Reason**: {reason}\n"
            
            report = warning.copy()
            warning.set_footer(text=(
                "There is no ticket system for support on this server.\n"
                "Ignore this thread and any invites or DMs you may receive."
            ))
            
            report.description += (
                "\n"
                f"Thread Name: `{thread.name}`\n"
                f"User ID: `{thread.owner}` ({thread.owner.mention})\n"
                f"Thread ID: `{thread.id}` ({thread.jump_url})\n"
                "\n"
                "Please review and take appropriate action."
            )
            
            await self.db.scam_reports.insert_one({
                "guild_id"   : thread.guild.id,
                "channel_id" : thread.id,
                "message_id" : None,
                "user_id"    : thread.owner_id,
                "reason"     : reason,
                "content"    : thread.name,
                "warning_id" : None,
                "report_id"  : None,
                "user_banned": False,
                "removed"    : False,
            })
            return warning, report

    async def report_message(self, message: Message, reason: str) -> None:
        if not (components := await self._generate_message_report(message, reason)):
            return None
        
        warning, report, contents = components

        try:
            view = self.DeletableView(self, message)
            warning_msg = await message.reply(embed=warning, view=view, mention_author=False)
        except errors.Forbidden:
            warning_msg = None
            log.warning(f"Failed to send warning message in reply to {message.id}")

        report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])
        report_msg = await report_channel.send(embed=report, file=contents)

        await self.db.scam_reports.update_one(
            {"message_id": message.id},
            {"$set": {"warning_id": warning_msg.id if warning_msg else None, "report_id": report_msg.id}}
        )
        return None  

    async def manual_report(self, interaction: Interaction, message: Message) -> None:
        await interaction.response.defer(ephemeral=True)
        
        if message.author.bot:
            return await interaction.followup.send(content="Bot messages can't be reported.", ephemeral=True)

        if message.author == interaction.user:
            return await interaction.followup.send(content="Did you just report yourself?", ephemeral=True)

        reporter = await self.bot.get_or_fetch_user(interaction.user.id)
        reason = f"Manual report by {reporter.mention}"

        if not (components := await self._generate_message_report(message, reason)):
            return await interaction.followup.send(
                content="Failed to report message. It may have already been reported or deleted.", 
                ephemeral=True
            )

        try:
            warning, report, contents = components
            report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])
            report_msg = await report_channel.send(embed=report, file=contents)
            moderator = await self.bot.get_or_fetch_user(cfg["rocketpool.support.moderator_id"])
            view = self.DeletableView(self, message)
            warning_msg = await message.reply(
                content=f"{moderator.mention} {report_msg.jump_url}",
                embed=warning,
                view=view,
                mention_author=False
            )
            await self.db.scam_reports.update_one(
                {"message_id": message.id},
                {"$set": {"warning_id": warning_msg.id, "report_id": report_msg.id}}
            )
            await interaction.followup.send(content="Thanks for reporting!", ephemeral=True)
        except Exception as e:
            await self.bot.report_error(e)
            await interaction.followup.send(
                content="Failed to send report details! The error has been reported.", 
                ephemeral=True
            )

        return None

    def _markdown_link_trick(self, message: Message) -> Optional[str]:
        txt = DetectScam._get_message_content(message)
        for m in self.markdown_link_pattern.findall(txt):
            if "." in m[0] and m[0] != m[1]:
                return "Markdown link with possible domain in visible portion that does not match the actual domain"
        return None

    def _discord_invite(self, message: Message) -> Optional[str]:
        txt = DetectScam._get_message_content(message)
        if self.invite_pattern.search(txt):
            return "Invite to external server"
        return None

    def _link_and_keywords(self, message: Message) -> Optional[str]:
        # message contains one of the relevant keyword combinations and a link
        txt = DetectScam._get_message_content(message)
        if not self.basic_url_pattern.search(txt):
            return None

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
                ("help", "assistance", "service", "support")
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

        return "There is no ticket system in this server." if txt_contains(keywords) else None

    def _paperhands(self, message: Message) -> Optional[str]:
        # message contains the word "paperhand" and a link
        txt = DetectScam._get_message_content(message)
        # if has http and contains the word paperhand or paperhold
        if (any(x in txt for x in ["paperhand", "paperhold", "pages.dev", "web.app"]) and "http" in txt) or "pages.dev" in txt:
            return "High chance the linked website is a scam"
        return None

    # contains @here or @everyone but doesn't actually have the permission to do so
    def _mention_everyone(self, message: Message) -> Optional[str]:
        txt = DetectScam._get_message_content(message)
        if ("@here" in txt or "@everyone" in txt) and not message.author.guild_permissions.mention_everyone:
            return "Mentioned @here or @everyone without permission"
        return None

    async def _reaction_spam(self, reaction: Reaction, user: User) -> Optional[str]:    
        # user reacts to their own message multiple times in quick succession to draw attention
        # check if user is a bot
        if user.bot:
            log.debug(f"Ignoring reaction by bot {user.id}")
            return None

        # check if the reaction is by the same user that created the message
        if reaction.message.author != user:
            log.debug(f"Ignoring reaction by non-author {user.id}")
            return None

        # check if the message is new enough (we ignore any reactions on messages older than 5 minutes)
        if (reaction.message.created_at - datetime.now(timezone.utc)) > timedelta(minutes=5):
            log.debug(f"Ignoring reaction on old message {reaction.message.id}")
            return None

        # get all reactions on message
        reactions = self._message_react_cache.get(reaction.message.id)
        if reactions is None:
            reactions = {}
            for msg_reaction in reaction.message.reactions:
                reactions[msg_reaction.emoji] = {user async for user in msg_reaction.users()}
            self._message_react_cache[reaction.message.id] = reactions
        elif reaction.emoji not in reactions:
            reactions[reaction.emoji] = {user}
        else:
            reactions[reaction.emoji].add(user)

        reaction_count = len([r for r in reactions.values() if user in r and len(r) == 1])
        log.debug(f"{reaction_count} reactions on message {reaction.message.id}")
        # if there are 8 reactions done by the author of the message, report it
        return "Reaction spam by message author" if (reaction_count >= 8) else None
            
    @Cog.listener()
    async def on_message(self, message: Message) -> None:
        if message.author == self.bot.user:
            return
        
        if message.guild is None:
            return
        
        if message.guild.id != cfg["rocketpool.support.server_id"]:
            log.warning(f"Ignoring message in {message.guild.id})")
            return

        checks = [
            self._markdown_link_trick,
            self._link_and_keywords,
            self._paperhands,
            self._mention_everyone,
            self._discord_invite
        ]
        for check in checks:
            if reason := check(message):
                await self.report_message(message, reason)
                return
        
    @Cog.listener()
    async def on_reaction_add(self, reaction: Reaction, user: User) -> None:
        if reaction.message.guild.id != cfg["rocketpool.support.server_id"]:
            log.warning(f"Ignoring reaction in {reaction.message.guild.id}")
            return
        
        checks = [
            self._reaction_spam(reaction, user)
        ]
        for reason in await asyncio.gather(*checks):
            if reason:
                await self.report_message(reaction.message, reason)
                return

    @Cog.listener()
    async def on_raw_message_delete(self, event: RawMessageDeleteEvent) -> None:
        async with self._update_lock:
            await self._on_message_delete(event.message_id)

    @Cog.listener()
    async def on_raw_bulk_message_delete(self, event: RawBulkMessageDeleteEvent) -> None:
        async with self._update_lock:
            await asyncio.gather(*[self._on_message_delete(msg_id) for msg_id in event.message_ids])

    async def _on_message_delete(self, message_id: int) -> None:
        report = await self.db.scam_reports.find_one({"message_id": message_id, "removed": False})
        if not report:
            return

        # delete warning message
        channel = await self.bot.get_or_fetch_channel(report["channel_id"])
        with contextlib.suppress(errors.NotFound, errors.Forbidden):
            message = await channel.fetch_message(report["warning_id"])
            await message.delete()

        await self._update_report(report, "Original message has been deleted.")
        await self.db.scam_reports.update_one(report, {"$set": {"warning_id": None, "removed": True}})

    @Cog.listener()
    async def on_member_ban(self, guild: Guild, user: User) -> None:
        async with self._update_lock:
            reports = await self.db.scam_reports.find(
                {"guild_id": guild.id, "user_id": user.id, "user_banned": False}
            ).to_list(None)
            for report in reports:
                await self._update_report(report, "User has been banned.")
                await self.db.scam_reports.update_one(report, {"$set": {"user_banned": True}})

    async def _update_report(self, report: dict, note: str) -> None:
        report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])
        try:
            message = await report_channel.fetch_message(report["report_id"])
            embed = message.embeds[0]
            embed.description += f"\n\n**{note}**"
            embed.color = self.Color.WARN if (embed.color == self.Color.ALERT) else self.Color.OK
            await message.edit(embed=embed)
        except Exception as e:
            await self.bot.report_error(e)

    async def report_thread(self, thread: Thread, reason: str) -> None:
        if not (components := await self._generate_thread_report(thread, reason)):
            return None
        
        warning, report = components
        
        try:
            view = self.DeletableView(self, thread)
            warning_msg = await thread.send(embed=warning, view=view)
        except errors.Forbidden:
            log.warning(f"Failed to send warning message in thread {thread.id}")
            warning_msg = None

        report_channel = await self.bot.get_or_fetch_channel(cfg["discord.channels.report_scams"])
        report_msg = await report_channel.send(embed=report)

        await self.db.scam_reports.update_one(
            {"channel_id": thread.id, "message_id": None},
            {"$set": {"warning_id": warning_msg.id if warning_msg else None, "report_id": report_msg.id}}
        )
        return None

    @Cog.listener()
    async def on_thread_create(self, thread: Thread) -> None:
        if thread.guild.id != cfg["rocketpool.support.server_id"]:
            log.warning(f"Ignoring thread creation in {thread.guild.id}")
            return

        keywords = ("support", "ticket", "assistance", "🎫", "🎟️")
        if not any(kw in thread.name.lower() for kw in keywords):
            log.debug(f"Ignoring thread creation (id: {thread.id}, name: {thread.name})")
            return
        
        await self.report_thread(thread, "Illegitimate support thread")
        
    @Cog.listener()
    async def on_raw_thread_update(self, event: RawThreadUpdateEvent) -> None:
        thread: Thread = await self.bot.get_or_fetch_channel(event.thread_id)
        await self.on_thread_create(thread)
    
    @Cog.listener()
    async def on_raw_thread_delete(self, event: RawThreadDeleteEvent) -> None:
        async with self._update_lock:
            report = await self.db.scam_reports.find_one(
                {"channel_id": event.thread_id, "message_id": None, "removed": False}
            )
            if not report:
                return

            await self._update_report(report, "Thread has been deleted.")
            await self.db.scam_reports.update_one(
                {"channel_id": event.thread_id, "message_id": None, "removed": False},
                {"$set": {"warning_id": None, "removed": True}}
            )


async def setup(bot):
    await bot.add_cog(DetectScam(bot))
