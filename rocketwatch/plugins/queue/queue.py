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


def get_queue(l=15):
    # Get the next n minipools per category
    minipools = rp.get_minipools(limit=l)
    description = ""
    matchings = [
        ["variable", "Variable Minipool Queue"],
    ]
    for category, label in matchings:
        data = minipools[category]
        if data[1]:
            if description:
                description += "\n"
            description += f"**{label}:** ({data[0]} Minipools)"
            for i, m in enumerate(data[1]):
                n = rp.call("rocketMinipool.getNodeAddress", address=m)
                t = rp.call("rocketMinipool.getStatusTime", address=m)
                description += f"\n`#{i+1}` {el_explorer_url(m, make_code=True, prefix=-1)}, created <t:{t}:R> by {el_explorer_url(n)}"
            if data[0] > 10:
                description += "\n`...`"

    return description


class Queue(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def queue(self, ctx: Context):
        """Show the next 10 minipools in the queue."""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        e.title = "Minipool queue"
        e.description = get_queue()
        # set gif if all queues are empty
        if not e.description:
            e.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Queue(bot))
