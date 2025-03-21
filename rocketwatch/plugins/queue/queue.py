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
    def get_minipool_queue(limit: int, start: int = 0) -> tuple[int, str]:
        """Get the next {limit} minipools in the queue"""

        queue_contract = rp.get_contract_by_name("addressQueueStorage")
        key = w3.soliditySha3(["string"], ["minipools.available.variable"])
        q_len = queue_contract.functions.getLength(key).call()

        start = max(start, 0)
        limit = min(limit, q_len - start)

        if limit <= 0:
            return 0, ""

        queue: list[ChecksumAddress] = [
            w3.to_checksum_address(res.results[0]) for res in rp.multicall.aggregate([
                queue_contract.functions.getItem(key, i) for i in range(start, start + limit)
            ]).results
        ]
        mp_contracts = [rp.assemble_contract("rocketMinipool", address=minipool) for minipool in queue]
        nodes: list[ChecksumAddress] = [
            w3.to_checksum_address(res.results[0]) for res in rp.multicall.aggregate([
                contract.functions.getNodeAddress() for contract in mp_contracts
            ]).results
        ]
        status_times: list[int] = [
            res.results[0] for res in rp.multicall.aggregate([
                contract.functions.getStatusTime() for contract in mp_contracts
            ]).results
        ]

        def as_code(label: str) -> str:
            return f"`{label}`"

        description = ""
        for i, minipool in enumerate(queue[:limit]):
            mp_label = el_explorer_url(minipool, name_fmt=as_code, prefix=-1)
            node_label = el_explorer_url(nodes[i])
            description += f"{i+1}. {mp_label} :construction_site: <t:{status_times[i]}:R> by {node_label}\n"

        if q_len > len(queue):
            description += "`...`"

        return q_len, description

    @hybrid_command()
    async def queue(self, ctx: Context):
        """Show the next 15 minipools in the queue"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        embed = Embed(title="Minipool Queue")
        queue_length, queue_description = self.get_minipool_queue(15)
        if queue_length:
            embed.description = queue_description
        else:
            embed.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Queue(bot))
