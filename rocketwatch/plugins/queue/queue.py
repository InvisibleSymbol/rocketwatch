import logging

from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from eth_typing import ChecksumAddress

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed
from utils.embeds import el_explorer_url
from utils.rocketpool import rp
from utils.visibility import is_hidden_weak
from utils.shared_w3 import w3

log = logging.getLogger("queue")
log.setLevel(cfg["log_level"])


class Queue(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @staticmethod
    def get_minipool_queue(limit: int) -> Embed:
        """Get the next {limit} minipools in the queue"""

        embed = Embed(title="Minipool Queue")

        queue_contract = rp.get_contract_by_name("addressQueueStorage")
        key = w3.soliditySha3(["string"], ["minipools.available.variable"])
        q_len = queue_contract.functions.getLength(key).call()
        limit = min(limit, q_len)

        if limit <= 0:
            embed.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
            return embed

        queue: list[ChecksumAddress] = [w3.to_checksum_address(res.results[0]) for res in rp.multicall.aggregate([
            queue_contract.functions.getItem(key, i) for i in range(limit)
        ]).results]
        mp_contracts = [rp.assemble_contract("rocketMinipool", address=minipool) for minipool in queue]
        nodes: list[ChecksumAddress] = [w3.to_checksum_address(res.results[0]) for res in rp.multicall.aggregate([
            contract.functions.getNodeAddress() for contract in mp_contracts
        ]).results]
        status_times: list[int] = [res.results[0] for res in rp.multicall.aggregate([
            contract.functions.getStatusTime() for contract in mp_contracts
        ]).results]

        def as_code(label: str) -> str:
            return f"`{label}`"

        embed.description = ""
        for i, minipool in enumerate(queue[:limit]):
            mp_label = el_explorer_url(minipool, name_fmt=as_code, prefix=-1)
            node_label = el_explorer_url(nodes[i], name_fmt=as_code)
            embed.description += f"{i+1}. {mp_label} :construction_site: <t:{status_times[i]}:R> `by` {node_label}\n"

        if q_len > len(queue):
            embed.description += "`...`"

        return embed

    @hybrid_command()
    async def queue(self, ctx: Context):
        """Show the next 15 minipools in the queue."""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = self.get_minipool_queue(limit=15)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Queue(bot))
