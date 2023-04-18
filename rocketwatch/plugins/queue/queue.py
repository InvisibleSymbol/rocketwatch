import logging

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils.cfg import cfg
from utils.embeds import Embed
from utils.embeds import el_explorer_url
from utils.rocketpool import rp
from utils.visibility import is_hidden

log = logging.getLogger("queue")
log.setLevel(cfg["log_level"])


def get_queue():
    e = Embed()
    e.title = "Minipool queue"

    # Get the next 10 minipools per category
    minipools = rp.get_minipools(limit=10)
    description = ""
    matchings = [
        ["variable", "Variable Minipool Queue"],
    ]
    for category, label in matchings:
        data = minipools[category]
        if data[1]:
            description += f"**{label}:** ({data[0]} Minipools)"
            description += "\n- "
            description += "\n- ".join([el_explorer_url(m, f'`{m}`') for m in data[1]])
            if data[0] > 10:
                description += "\n- ..."
            description += "\n\n"

    # set gif if all queues are empty
    if not description:
        e.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
    else:
        e.description = description

    return e


class Queue(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def queue(self, ctx: Context):
        """Show the next 10 minipools in the queue."""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = get_queue()
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Queue(bot))
