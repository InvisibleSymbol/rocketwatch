import logging
from datetime import timedelta, datetime
from functools import cached_property

from discord import errors
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from utils.cfg import cfg
from utils.embeds import Embed
from utils.get_or_fetch import get_or_fetch_channel


log = logging.getLogger("scam_warning")
log.setLevel(cfg["log_level"])


class ScamWarning(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.cooldown_time = timedelta(days=90)

    async def send_warning(self, user) -> None:
        report_channel = await get_or_fetch_channel(self.bot, cfg["rocketpool.support.channel_id"])
        support_channel = await get_or_fetch_channel(self.bot, cfg["discord.channels.report_scams"])
        resource_channel = await get_or_fetch_channel(self.bot, cfg["discord.channels.resources"])

        embed = Embed()
        embed.title = "**Stay Safe on Rocket Pool Discord**"
        embed.description = (
            f"Hello! You've recently been active on the Rocket Pool server and might have opened "
            f"your direct messages (DMs) to other members or users. To protect your funds and stay secure, "
            f"please follow these guidelines:\n"
            f"\n"
            f"1. **Keep Conversations Public**\n"
            f"  - Ask and answer questions in public channels whenever possible.\n"
            f"  - Ignore unsolicited DMs from strangers.\n"
            f"  - **Beware of support threads**\n"
            f"      - Scammers may ping you from newly-created threads pretending to offer support.\n"
            f"      - Be cautious if someone contacts you directly from a thread.\n"
            f"  - Report any suspicious messages in {report_channel.mention}.\n"
            f"\n"
            f"2. **Use Official Resources Only**\n"
            f"  - Avoid joining external Discord servers or visiting unknown websites that claim to offer support.\n"
            f"  - We do **not** use a ticket system. For assistance, please use {support_channel.mention}.\n"
            f"  - Always double-check links, even if they are shared publicly.\n"
            f"  - You can find official Rocket Pool links and contract addresses in {resource_channel.mention}.\n"
            f"  - If you're unsure about something, wait for confirmation from the community.\n"
            f"\n"
            f"3. **Protect Your Private Information**\n"
            f"  - Never share your private keys or seed phrase with anyone.\n"
            f"  - This information is **never** needed to help resolve issues.\n"
            f"\n"
            f"4. **Be Cautious with Private Messages**\n"
            f"  - If you need to share sensitive details like your wallet address in private, be extra careful.\n"
            f"  - Initiate the conversation yourself to avoid imposters.\n"
            f"  - Verify the person's identity with others if necessary.\n"
            f"\n"
            f"**Tip:** Consider changing your Discord settings to limit who can send you direct messages. "
            f"This can help prevent unwanted interactions.\n"
            f"\n"
            f"*This message may be sent again as a reminder after periods of inactivity.*"
        )
        await user.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message) -> None:
        # don't let the bot try to DM itself
        msg_author = message.author
        if msg_author == self.bot.user:
            return

        if msg_author.guild_permissions.moderate_members:
            log.info(f"{message.author} is a moderator, skipping warning.")
            return

        msg_time = message.created_at.replace(tzinfo=None)
        db_entry = await self.db.scam_warning.find_one({"_id": msg_author.id})

        # only send if user's last interaction is not within cooldown time
        if (db_entry is None) or (msg_time - db_entry["last_message"]) > self.cooldown_time:
            try:
                await self.send_warning(msg_author)
            except errors.Forbidden:
                log.info(f"Unable to DM {message.author}, skipping warning.")
                return

        await self.db.scam_warning.replace_one(
            {"_id": msg_author.id},
            {"_id": msg_author.id, "last_message": message.created_at},
            upsert=True
        )


async def setup(bot):
    await bot.add_cog(ScamWarning(bot))
