import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Literal, cast

import aiohttp
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from discord.app_commands import Choice, choices

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden_weak
from utils.retry import retry_async

log = logging.getLogger("forum")
log.setLevel(cfg["log_level"])


class Forum(commands.Cog):
    DOMAIN = "https://dao.rocketpool.net"

    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @dataclass(frozen=True, slots=True)
    class Topic:
        id: int
        title: str
        slug: str
        post_count: int
        created_at: int
        last_post_at: int
        views: int
        like_count: int

        @property
        def url(self) -> str:
            return f"{Forum.DOMAIN}/t/{self.slug}"

        def __str__(self) -> str:
            return self.title

    @dataclass(frozen=True, slots=True)
    class User:
        id: int
        username: str
        name: Optional[str]
        topic_count: int
        post_count: int
        likes_received: int

        @property
        def url(self):
            return f"{Forum.DOMAIN}/u/{self.username}"

        def __str__(self) -> str:
            return self.name or self.username

    Period = Literal["all", "yearly", "quarterly", "monthly", "weekly", "daily"]
    UserMetric = Literal["topic_count", "post_count", "likes_received"]

    @staticmethod
    def _parse_topics(topic_list: list[dict]) -> list[Topic]:
        def datetime_to_epoch(_dt: str) -> int:
            return int(datetime.fromisoformat(_dt.replace("Z", "+00:00")).timestamp())

        topics = []
        for topic_dict in topic_list:
            topics.append(Forum.Topic(
                id=topic_dict["id"],
                title=topic_dict["fancy_title"],
                slug=topic_dict["slug"],
                post_count=topic_dict["posts_count"],
                created_at=datetime_to_epoch(topic_dict["created_at"]),
                last_post_at=datetime_to_epoch(topic_dict["last_posted_at"]),
                views=topic_dict["views"],
                like_count=topic_dict["like_count"]
            ))
        return topics

    @staticmethod
    @retry_async(tries=3, delay=1)
    async def get_popular_topics(period: Period) -> list[Topic]:
        async with aiohttp.ClientSession() as session:
            response = await session.get(f"{Forum.DOMAIN}/top.json?period={period}")
            data = await response.json()

        return Forum._parse_topics(data["topic_list"]["topics"])

    @staticmethod
    @retry_async(tries=3, delay=1)
    async def get_recent_topics() -> list[Topic]:
        async with aiohttp.ClientSession() as session:
            response = await session.get(f"{Forum.DOMAIN}/latest.json")
            data = await response.json()

        return Forum._parse_topics(data["topic_list"]["topics"])

    @staticmethod
    @retry_async(tries=3, delay=1)
    async def get_top_users(period: Period, order_by: UserMetric) -> list[User]:
        async with aiohttp.ClientSession() as session:
            response = await session.get(f"{Forum.DOMAIN}/directory_items.json?period={period}&order={order_by}")
            data = await response.json()

        users = []
        for user_dict in data["directory_items"]:
            users.append(Forum.User(
                id=user_dict["id"],
                username=user_dict["user"]["username"],
                name=user_dict["user"]["name"] if user_dict["user"]["name"] else None,
                topic_count=user_dict["topic_count"],
                post_count=user_dict["post_count"] - user_dict["topic_count"],
                likes_received=user_dict["likes_received"]
            ))
        return users

    @hybrid_command()
    async def top_forum_posts(
        self,
        ctx: Context,
        period: Period = "monthly",
    ) -> None:
        """Get the most popular topics from the forum"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        if isinstance(period, Choice):
            period: Forum.Period = cast(Forum.Period, period.value)

        embed = Embed(title=f"Top Forum Posts ({period.capitalize()})", description="")

        if topics := await self.get_popular_topics(period):
            for i, topic in enumerate(topics[:10], start=1):
                embed.description += (
                    f"{i}. [{topic}]({topic.url})\n"
                    f"Last reply: <t:{topic.last_post_at}:R>\n"
                    f"`{topic.like_count:>4}` 🤍\t`{topic.post_count:>4}` 💬\t`{topic.views:>4}` 👀\n"
                )
        else:
            embed.description = "No topics found."

        await ctx.send(embed=embed)

    @hybrid_command()
    async def top_forum_users(
        self,
        ctx: Context,
        period: Period = "monthly",
        order_by: UserMetric = "likes_received"
    ) -> None:
        """Get the most active forum users"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        embed = Embed(
            title=f"Top Forum Users ({period.capitalize()})",
            description=""
        )

        users = await self.get_top_users(period, order_by)
        if users:
            for i, user in enumerate(users[:10], start=1):
                embed.description += (
                    f"{i}. [{user}]({user.url})\n"
                    f"`{user.likes_received:>4}` 🤍\t`{user.topic_count:>4}` 📝\t`{user.post_count:>4}` 💬\n"
                )
        else:
            embed.description = "No users found."

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Forum(bot))
