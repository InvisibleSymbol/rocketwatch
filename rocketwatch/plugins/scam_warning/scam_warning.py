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

    @cached_property
    async def _support_channel(self):
        return await get_or_fetch_channel(self.bot, cfg["rocketpool.support.channel_id"])

    @cached_property
    async def _report_channel(self):
        return await get_or_fetch_channel(self.bot, cfg["discord.channels.report_scams"])

    @cached_property
    async def _resource_channel(self):
        return await get_or_fetch_channel(self.bot, cfg["discord.channels.resources"])

    async def send_warning(self, user) -> None:
        report_channel = await self._report_channel
        support_channel = await self._support_channel
        resource_channel = await self._resource_channel

        embed = Embed()
        embed.title = ":warning: Beware of Scams :warning:"
        embed.description = (
            "If you're seeing this message, you recently interacted with the Rocket Pool server and your direct "
            "messages are open to members of the server, or even any Discord user. Please note the following "
            "guidelines to avoid losing funds to a scam in the future.\n"
            "\n"
            "Questions and support inquiries should be responded to in public channels in almost all cases. "
            "If you receive unsolicited DMs, ignore the messages and report the interaction in "
            f"{report_channel.mention}.\n"
            "\n"
            "**DO NOT** join external servers or even supposed support threads that may be created within "
            "the Rocket Pool server. Likewise, **do not** interact with websites that claim to provide support. "
            f"There is no ticket system, people will be happy to assist you in {support_channel.mention}.\n"
            "\n"
            "**DO NOT under any circumstances** reveal your seed phrase or private key in messages or other support "
            "interactions. For websites that require contract interactions, do not blindly trust links, even if "
            f"they were posted publicly. Rocket Pool specific URLs can be found in {resource_channel.mention}. "
            "If unsure, wait for other server members to confirm the link can be trusted.\n"
            "\n"
            "In some cases, you may prefer to convey information, such as your wallet address, privately. "
            "If so, it is critical to ensure the person you're interacting with can be trusted. "
            "Also here, asking other members for confirmation can be helpful. In addition, you should initiate the "
            "conversation yourself. This minimizes the risk of interacting with scammers that may impersonate "
            "other members of the server.\n"
            "\n"
            "To avoid unwanted interactions, consider restricting who can message you directly in Discord settings. "
            "This message may be re-sent as a reminder after long periods of inactivity."
        )
        await user.send(embed=embed)

    async def maybe_send_warning(self, message) -> None:
        # don't let the bot try to DM itself
        msg_author = message.author
        if msg_author == self.bot.user:
            return

        # only send if it's the first interaction in at least 90 days
        msg_time = message.created_at.replace(tzinfo=None)
        db_entry = await self.db.scam_warning.find_one({"_id": msg_author.id})
        if (db_entry is None) or (msg_time - db_entry["last_message"]) >= timedelta(days=90):
            await self.send_warning(msg_author)

        await self.db.scam_warning.replace_one(
            {"_id": msg_author.id},
            {"_id": msg_author.id, "last_message": message.created_at},
            upsert=True
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        try:
            await self.maybe_send_warning(message)
        except errors.Forbidden:
            log.info(f"Unable to DM {message.author}, no need to warn them.")


async def setup(bot):
    await bot.add_cog(ScamWarning(bot))
