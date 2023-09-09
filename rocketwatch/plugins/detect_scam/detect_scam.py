import io

import regex as re
from discord import File
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import Embed
from utils.get_or_fetch import get_or_fetch_channel


class DetectScam(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    async def report_suspicious_message(self, msg, reason):
        e = Embed(title="🚨 Warning: Possible Scam Detected")
        e.description = f"**Reason:** {reason}\n"
        bak_footer = e.footer.text
        e.set_footer(text="This message will be deleted once the suspicious message is removed.")
        warning = await msg.reply(embed=e, mention_author=False)
        e.set_footer(text=bak_footer)
        # report into report-scams channel as well
        ch = await get_or_fetch_channel(self.bot, cfg["discord.channels.report_scams"])
        e.description += f"User ID: `{msg.author.id}`\nMessage ID: `{msg.id}` ({msg.jump_url})\nChannel ID: `{msg.channel.id}` ({msg.channel.mention})`\n\n"
        e.description += "Original message has been attached as a file. Please review and take appropriate action."
        with io.StringIO(msg.content) as f:
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

    @commands.Cog.listener()
    async def on_message(self, message):
        r = re.compile(r"(?<=\[)([^/\] ]*).+?(?<=\(https?:\/\/)([^/\)]*)")
        matches = r.findall(message.content)
        for m in matches:
            if "." in m[0] and m[0] != m[1]:
                await self.report_suspicious_message(message,
                                                     "Markdown link with possible domain in visible portion that does not match the actual domain")

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        # check if message was reported
        report = await self.db["scam_reports"].find_one({"guild_id": message.guild.id, "message_id": message.id})
        if report:
            # delete warning message
            ch = await get_or_fetch_channel(self.bot, report["channel_id"])
            msg = await ch.fetch_message(report["warning_id"])
            await ch.delete_messages([msg])
            # record in db that message was deleted
            await self.db["scam_reports"].update_one({"guild_id": message.guild.id, "message_id": message.id},
                                                     {"$set": {"deleted": True}})


async def setup(bot):
    await bot.add_cog(DetectScam(bot))
