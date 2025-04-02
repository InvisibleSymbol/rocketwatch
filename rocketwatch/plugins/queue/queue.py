import math
import logging

from functools import cache
from discord import ui, ButtonStyle, Interaction
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from eth_typing import ChecksumAddress

from rocketwatch import RocketWatch
from utils import solidity
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

    class PageView(ui.View):
        PAGE_SIZE = 15

        def __init__(self):
            super().__init__(timeout=None)
            self.page_index = 0

        async def load(self) -> Embed:
            queue_length, queue_content = Queue.get_minipool_queue(
                limit=self.PAGE_SIZE, start=(self.page_index * self.PAGE_SIZE)
            )
            max_page_index = int(math.floor(queue_length / self.PAGE_SIZE))

            if self.page_index > max_page_index:
                # if the queue changed and this is out of bounds, try again
                self.page_index = max_page_index
                return await self.load()

            embed = Embed(title="Minipool Queue")
            if queue_length > 0:
                embed.description = queue_content
                self.prev_page.disabled = (self.page_index <= 0)
                self.next_page.disabled = (self.page_index >= max_page_index)
            else:
                embed.set_image(url="https://c.tenor.com/1rQLxWiCtiIAAAAd/tenor.gif")
                self.clear_items() # remove buttons

            return embed

        @ui.button(emoji="⬅", label="Prev", style=ButtonStyle.gray)
        async def prev_page(self, interaction: Interaction, _) -> None:
            self.page_index -= 1
            embed = await self.load()
            await interaction.response.edit_message(embed=embed, view=self)

        @ui.button(emoji="➡", label="Next", style=ButtonStyle.gray)
        async def next_page(self, interaction: Interaction, _) -> None:
            self.page_index += 1
            embed = await self.load()
            await interaction.response.edit_message(embed=embed, view=self)

    @staticmethod
    @cache
    def _cached_el_url(address, prefix="") -> str:
        return el_explorer_url(address, name_fmt=lambda n: f"`{n}`", prefix=prefix)

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

        content = ""
        for i, minipool in enumerate(queue[:limit]):
            mp_label = Queue._cached_el_url(minipool, -1)
            node_label = Queue._cached_el_url(nodes[i])
            content += f"{start+i+1}. {mp_label} :construction_site: <t:{status_times[i]}:R> by {node_label}\n"

        return q_len, content

    @hybrid_command()
    async def queue(self, ctx: Context):
        """Show the minipool queue"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        view = Queue.PageView()
        embed = await view.load()
        await ctx.send(embed=embed, view=view)

    @hybrid_command()
    async def clear_queue(self, ctx: Context):
        """Show gas price for clearing the queue using the rocketDepositPoolQueue contract"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        e = Embed(title="Gas Prices for Dequeuing Minipools")
        e.set_author(
            name="🔗 Forum: Clear minipool queue contract",
            url="https://dao.rocketpool.net/t/clear-minipool-queue-contract/670"
        )

        queue_length = rp.call("rocketMinipoolQueue.getTotalLength")
        dp_balance = solidity.to_float(rp.call("rocketDepositPool.getBalance"))
        match_amount = solidity.to_float(rp.call("rocketDAOProtocolSettingsMinipool.getVariableDepositAmount"))
        max_dequeues = min(int(dp_balance / match_amount), queue_length)

        if max_dequeues > 0:
            max_assignments = rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositAssignments")
            min_assignments = rp.call("rocketDAOProtocolSettingsDeposit.getMaximumDepositSocialisedAssignments")

            # half queue clear
            half_clear_count = int(max_dequeues / 2)
            half_clear_input = max_assignments * math.ceil(half_clear_count / min_assignments)
            gas = rp.estimate_gas_for_call("rocketDepositPoolQueue.clearQueueUpTo", half_clear_input)
            e.add_field(
                name=f"Half Clear ({half_clear_count} MPs)",
                value=f"`clearQueueUpTo({half_clear_input})`\n `{gas:,}` gas"
            )

            # full queue clear
            full_clear_size = max_dequeues
            full_clear_input = max_assignments * math.ceil(full_clear_size / min_assignments)
            gas = rp.estimate_gas_for_call("rocketDepositPoolQueue.clearQueueUpTo", full_clear_input)
            e.add_field(
                name=f"Full Clear ({full_clear_size} MPs)",
                value=f"`clearQueueUpTo({full_clear_input})`\n `{gas:,}` gas"
            )
        elif queue_length > 0:
            e.description = "Not enough funds in deposit pool to dequeue any minipools."
        else:
            e.description = "Queue is empty."

        # link to contract
        e.add_field(
            name="Contract",
            value=el_explorer_url(rp.get_address_by_name('rocketDepositPoolQueue'), "RocketDepositPoolQueue"),
            inline=False
        )

        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Queue(bot))
