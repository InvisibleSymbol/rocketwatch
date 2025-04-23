import math
from abc import abstractmethod

from discord import ui, ButtonStyle, Interaction
from utils.embeds import Embed

class PageView(ui.View):
    def __init__(self, page_size: int):
        super().__init__(timeout=None)
        self.page_index = 0
        self.page_size = page_size
        
    @property
    @abstractmethod
    def _title(self) -> str:
        pass
        
    @abstractmethod
    async def _load_content(self, from_idx: int, to_idx: int) -> tuple[int, str]:
        pass

    async def load(self) -> Embed:
        num_items, content = await self._load_content(
            (self.page_index * self.page_size),
            ((self.page_index + 1) * self.page_size - 1)
        )
        
        embed = Embed(title=self._title)
        if num_items <= 0:
            embed.set_image(url="https://c.tenor.com/1rQLxWiCtiIAAAAd/tenor.gif")
            self.clear_items() # remove buttons
            return embed

        max_page_index = int(math.ceil(num_items / self.page_size)) - 1
        if self.page_index > max_page_index:
            # if the content changed and this is out of bounds, try again
            self.page_index = max_page_index
            return await self.load()

        embed.description = content
        self.prev_page.disabled = (self.page_index <= 0)
        self.next_page.disabled = (self.page_index >= max_page_index)            
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
