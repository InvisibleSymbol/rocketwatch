import logging
from datetime import datetime

import aiohttp
from discord.app_commands import Choice, choices
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden

log = logging.getLogger("forum")
log.setLevel(cfg["log_level"])


class Forum(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.domain = "https://dao.rocketpool.net"

    @hybrid_command()
    @choices(
        period=[
            Choice(name="all time", value="all"),
            Choice(name="yearly", value="yearly"),
            Choice(name="quarterly", value="quarterly"),
            Choice(name="monthly", value="monthly"),
            Choice(name="weekly", value="weekly"),
            Choice(name="daily", value="daily")
        ],
        user_order_by=[
            Choice(name="likes", value="likes_received"),
            Choice(name="replies sent", value="post_count"),
            Choice(name="posts created", value="topic_count"),
        ]
    )
    async def top_forum_posts(self, ctx: Context, period: Choice[str] = "monthly",
                              user_order_by: Choice[str] = "likes_received"):
        """
        Get the top posts from the forum.
        """
        await ctx.defer(ephemeral=is_hidden(ctx))
        if isinstance(period, Choice):
            period = period.value
        if isinstance(user_order_by, Choice):
            user_order_by = user_order_by.value

        # retrieve the top posts from the forum for the specified period
        async with aiohttp.ClientSession() as session:
            res = await session.get(f"{self.domain}/top.json?period={period}")
            res = await res.json()

        # create the embed
        e = Embed()
        e.title = f"Top Forum Stats ({period})"
        # top 10 topics
        tmp_desc = "\n".join(
            f"{i + 1}. [{topic['fancy_title']}]({self.domain}/t/{topic['slug']})\n"
            f"Last Reply: <t:{int(datetime.fromisoformat(topic['last_posted_at'].replace('Z', '+00:00')).timestamp())}:R>\n"
            f"`{topic['like_count']:>4}` 🤍\t "
            f"`{topic['posts_count'] - 1:>4}` 💬\t"
            f"`{topic['views']:>4}` 👀\n "
            for i, topic in enumerate(res["topic_list"]["topics"][:5]))
        e.add_field(name=f"Top {min(5, len(res['topic_list']['topics']))} Topics", value=tmp_desc or "No topics found.", inline=False)

        async with aiohttp.ClientSession() as session:
            res = await session.get(f"{self.domain}/directory_items.json?period={period}&order={user_order_by}")
            res = await res.json()
        # top 5 users
        tmp_desc = "".join(
            f"{i + 1}. [{meta['user']['name'] or meta['user']['username']}]"
            f"({self.domain}/u/{meta['user']['username']})\n"
            f"`{meta['likes_received']:>4}` 🤍\t "
            f"`{meta['post_count'] - meta['topic_count']:>4}` 💬\t "
            f"`{meta['topic_count']:>4}` 📝\n"
            for i, meta in enumerate(res["directory_items"][:5]))
        e.add_field(name=f"Top {min(5, len(res['directory_items']))} Users by {user_order_by.replace('_', ' ')}", value=tmp_desc or "No users found.", inline=False)

        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Forum(bot))
