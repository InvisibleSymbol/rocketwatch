import logging

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed
from utils.embeds import el_explorer_url
from utils.rocketpool import rp
from utils.visibility import is_hidden_weak

log = logging.getLogger("queue")
log.setLevel(cfg["log_level"])


class Queue(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @staticmethod
    def get_minipool_queue(limit: int = 15) -> Embed:
        """Get the next {limit} minipools in the queue"""

        embed = Embed(title="Minipool Queue")

        mp_count, queue = rp.get_minipools(limit=limit)
        if not queue:
            embed.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
            return embed

        mp_contracts = [rp.assemble_contract("rocketMinipool", address=minipool) for minipool in queue]
        nodes = [
            res.results[0] for res in rp.multicall.aggregate([
                contract.functions.getNodeAddress() for contract in mp_contracts
            ]).results
        ]
        status_times = [
            res.results[0] for res in rp.multicall.aggregate([
                contract.functions.getStatusTime() for contract in mp_contracts
            ]).results
        ]

        embed.description = f"**Minipool Queue** ({mp_count})"
        for i, minipool in enumerate(queue):
            mp_label = el_explorer_url(minipool, make_code=True, prefix=-1)
            node_label = el_explorer_url(nodes[i])
            embed.description += f"\n{mp_label}, created <t:{status_times[i]}:R> by {node_label}"

        if mp_count > len(queue):
            embed.description += "\n`...`"

        return embed

    @hybrid_command()
    async def queue(self, ctx: Context):
        """Show the next 10 minipools in the queue."""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = self.get_minipool_queue()
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Queue(bot))
