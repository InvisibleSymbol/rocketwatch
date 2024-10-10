import asyncio
import contextlib
import io
import logging
from datetime import datetime

import regex as re
from datetime import timezone
from discord import File, errors, Color, DeletedReferencedMessage
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient
from cachetools import TTLCache

from utils.cfg import cfg
from utils.embeds import Embed
from utils.get_or_fetch import get_or_fetch_channel


log = logging.getLogger("detect_scam")
log.setLevel(cfg["log_level"])


def get_text_of_message(message):
    text = ""
    if message.content:
        text += message.content + "\n"
    if message.embeds:
        for embed in message.embeds:
            text += f"---\n Embed: {embed.title}\n{embed.description}\n---\n"
    return text.lower()


class DetectScam(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.reported_ids = set()
        self.report_lock = asyncio.Lock()
        self.reaction_lock = asyncio.Lock()
        self.message_react_cache = TTLCache(maxsize=1000, ttl=300)
        self.__markdown_link_pattern = re.compile(r"(?<=\[)([^/\] ]*).+?(?<=\(https?:\/\/)([^/\)]*)")
        self.__basic_url_pattern = re.compile(r"https?:\/\/([/\\@\-_0-9a-zA-Z]+\.)+[\\@\-_0-9a-zA-Z]+")
        self.__invite_pattern = re.compile(r"discordapp\.com\/invite\\(?P<code>[a-zA-Z0-9]+)")

    async def report_suspicious_message(self, msg, reason):
        # check if the message has been deleted
        try:
            msg = await msg.channel.fetch_message(msg.id)
            if isinstance(msg, DeletedReferencedMessage):
                return
        except errors.NotFound:
            return
        # lock
        async with self.report_lock:
            # check if message has already been reported
            if msg.id in self.reported_ids:
                return
            e = Embed(title="🚨 Warning: Possible Scam Detected")
            e.colour = Color.from_rgb(255, 0, 0)
            e.description = f"**Reason:** {reason}\n"
            bak_footer = e.footer.text
            e.set_footer(text="This message will be deleted once the suspicious message is removed.")
            # supress failure
            with contextlib.suppress(errors.Forbidden):
                warning = await msg.reply(embed=e, mention_author=False)
            e.set_footer(text=bak_footer)
            # report into report-scams channel as well
            ch = await get_or_fetch_channel(self.bot, cfg["discord.channels.report_scams"])
            e.description += f"User ID: `{msg.author.id}` ({msg.author.mention})\nMessage ID: `{msg.id}` ({msg.jump_url})\nChannel ID: `{msg.channel.id}` ({msg.channel.mention})\n\n"
            e.description += "Original message has been attached as a file. Please review and take appropriate action."
            with io.StringIO(get_text_of_message(msg)) as f:
                report = await ch.send(embed=e, file=File(f, filename="original_message.txt"))
            # insert back reference into database so we can delete it later if removed
            await self.db["scam_reports"].insert_one({
                "guild_id"  : msg.guild.id,
                "report_id" : report.id,
                "message_id": msg.id,
                "warning_id": warning.id,
                "user_id"   : msg.author.id,
                "channel_id": msg.channel.id,
                "reason"    : reason
            })
            # add to reported ids
            self.reported_ids.add(msg.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild.id != cfg["rocketpool.support.server_id"]:
            log.warning(f"Ignoring message from {message.guild.id} (Content: {message.content})")
            return
        # if self ignore
        if message.author.id == self.bot.user.id:
            return
        checks = [
            self.markdown_link_trick(message),
            self.ticket_with_link(message),
            self.paperhands(message),
            self.mention_everyone(message),
            self.discord_invite(message)
        ]
        await asyncio.gather(*checks)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if reaction.message.guild.id != cfg["rocketpool.support.server_id"]:
            log.warning(f"Ignoring reaction from {reaction.message.guild.id} (Content: {reaction.message.content})")
            return
        checks = [
            self.reaction_spam(reaction, user)
        ]
        await asyncio.gather(*checks)

    async def markdown_link_trick(self, message):
        txt = get_text_of_message(message)
        for m in self.__markdown_link_pattern.findall(txt):
            if "." in m[0] and m[0] != m[1]:
                await self.report_suspicious_message(
                    message,
                    "Markdown link with possible domain in visible portion that does not match the actual domain."
                )

    async def discord_invite(self, message):
        txt = get_text_of_message(message)
        if self.__invite_pattern.search(txt):
            await self.report_suspicious_message(
                message,
                "Invite to external server"
            )

    async def ticket_with_link(self, message):
        # message contains the word "ticket" and a link
        txt = get_text_of_message(message)
        has_url = self.__basic_url_pattern.search(txt)
        if has_url and any(x in txt for x in ["ticket", "support team", "admin", "mod team", "moderator"]):
            await self.report_suspicious_message(message, "There is no ticket system in this server.")

    async def paperhands(self, message):
        # message contains the word "paperhand" and a link
        txt = get_text_of_message(message)
        # if has http and contains the word paperhand or paperhold
        if (any(x in txt for x in ["paperhand", "paperhold", "pages.dev", "web.app"]) and "http" in txt) or "pages.dev" in txt:
            await self.report_suspicious_message(message, "High chance the linked website is a scam.")

    # contains @here or @everyone but doesn't actually have the permission to do so
    async def mention_everyone(self, message):
        txt = get_text_of_message(message)
        if ("@here" in txt or "@everyone" in txt) and not message.author.guild_permissions.mention_everyone:
            await self.report_suspicious_message(message, "Mentioned @here or @everyone without permission")


    async def reaction_spam(self, reaction, user):
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
    async def on_message_delete(self, message):
        # check if message was reported
        report = await self.db["scam_reports"].find_one({"guild_id": message.guild.id, "message_id": message.id})
        if report:
            # delete warning message
            ch = await get_or_fetch_channel(self.bot, report["channel_id"])
            with contextlib.suppress(errors.NotFound):
                msg = await ch.fetch_message(report["warning_id"])
                await ch.delete_messages([msg])
            # try to update report message to indicate that the message was deleted
            with contextlib.suppress(errors.NotFound):
                ch = await get_or_fetch_channel(self.bot, cfg["discord.channels.report_scams"])
                msg = await ch.fetch_message(report["report_id"])
                e = msg.embeds[0]
                e.description += "\n\n**Original message has been deleted.**"
                # orange
                e.colour = Color.from_rgb(255, 165, 0)
                await msg.edit(embed=e)
            # record in db that message was deleted
            await self.db["scam_reports"].update_one({"guild_id": message.guild.id, "message_id": message.id},
                                                     {"$set": {"deleted": True}})
        if message.channel.id == cfg["rocketpool.support.channel_id"]:
            e = Embed(title="Message Deleted")
            e.description = f"**User:** {message.author.mention} ({message.author.id})\n" \
                            f"**Channel:** {message.channel.mention} ({message.channel.id})\n" \
                            f"**Message:**\n{get_text_of_message(message)[:1000]}"
            e.set_footer(text=f"Message ID: {message.id}")
            ch = await get_or_fetch_channel(self.bot, 895367217288466482)
            await ch.send(embed=e)


    @commands.Cog.listener()
    # on user ban
    async def on_member_ban(self, guild, user):
        # delete all warnings, update all reports
        reports = await self.db["scam_reports"].find({"guild_id": guild.id, "user_id": user.id}).to_list(None)
        for report in reports:
            # delete warning message
            ch = await get_or_fetch_channel(self.bot, report["channel_id"])
            with contextlib.suppress(errors.NotFound):
                msg = await ch.fetch_message(report["warning_id"])
                await ch.delete_messages([msg])
            # try to update report message to indicate that the message was deleted
            with contextlib.suppress(errors.NotFound):
                ch = await get_or_fetch_channel(self.bot, cfg["discord.channels.report_scams"])
                msg = await ch.fetch_message(report["report_id"])
                e = msg.embeds[0]
                e.description += "\n\n**User has been banned.**"
                # green
                e.colour = Color.from_rgb(0, 255, 0)
                await msg.edit(embed=e)
            # record in db that message was deleted
            await self.db["scam_reports"].update_one({"guild_id": guild.id, "message_id": report["message_id"]},
                                                     {"$set": {"deleted": True}})

async def setup(bot):
    await bot.add_cog(DetectScam(bot))
