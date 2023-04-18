import asyncio
import logging
import random
import random as pyrandom

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden_weak

class EightBall(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command(name="8ball")
    async def eight_ball(self, ctx: Context, question: str):
        e = Embed(title="🎱 Magic 8 Ball")
        if not question.endswith("?"):
            e.description = "You must ask a yes or no question to the magic 8 ball (hint: add a `?` at the end of your question)"
            await ctx.send(embed=e, ephemeral=True)
            return
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        await asyncio.sleep(random.randint(2,5))
        res = pyrandom.choice([
            "As I see it, yes",
            "It is certain",
            "It is decidedly so",
            "Most likely",
            "Outlook good",
            "Signs point to yes",
            "Without a doubt",
            "Yes",
            "Yes - definitely",
            "You may rely on it",
            "Don't count on it",
            "My reply is no",
            "My sources say no",
            "Outlook not so good",
            "Very doubtful",
            "Chances aren't good",
            "Unlikely",
            "Not likely",
            "No",
            "Absolutely not"
        ])
        e.description = f"> \"{question}\"\n - `{ctx.author.display_name}`\n\nThe Magic 8 Ball says: `{res}`"
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(EightBall(bot))
