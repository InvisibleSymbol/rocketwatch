import logging
from datetime import datetime
from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.get_nearest_block import get_block_by_timestamp
from utils.readable import uptime
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.thegraph import get_active_snapshot_votes
from utils.visibility import is_hidden, is_hidden_weak
from utils.draw import BetterImageDraw

log = logging.getLogger("snapshot")
log.setLevel(cfg["log_level"])

RANK_COLORS = {
    # 1st rank, gold
    0: (255, 215, 0),
    # 2nd rank, silver
    1: (192, 192, 192),
    # 3rd rank, bronze
    2: (205, 127, 50),
}


class Snapshot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def votes(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        e = Embed()
        e.set_author(name="🔗 Data from snapshot.org", url="https://snapshot.org/#/delegate/rocketpool-dao.eth")
        proposals = get_active_snapshot_votes()
        if not proposals:
            e.description = "No active proposals"
            return await ctx.send(embed=e)

        # image width is based upon the number of proposals
        p_width = 400
        width = p_width * len(proposals)
        # image height is based upon the max number of possible options
        height = 50 * max(len(p["choices"]) for p in proposals) + 100
        # pillow image
        img = Image.new("RGB", (width, height), color=(40, 40, 40))
        # pillow draw
        draw = BetterImageDraw(img)
        # visualize the proposals
        for i, proposal in enumerate(proposals):
            x_offset = i * p_width
            y_offset = 20
            # draw the proposal title
            draw.dynamic_text(
                (x_offset + 10, y_offset),
                proposal["title"],
                20,
                max_width=p_width - 20,
            )
            y_offset += 40
            # order (choice, score) pairs by score
            choices = sorted(zip(proposal["choices"], proposal["scores"]), key=lambda x: x[1], reverse=True)
            for i, (choice, scores) in enumerate(choices):
                draw.dynamic_text(
                    (x_offset + 10, y_offset),
                    choice,
                    15,
                    max_width=p_width - 20 - 120,
                )
                # display the score as text, right aligned
                draw.dynamic_text(
                    (x_offset + p_width - 10, y_offset),
                    f"{scores:,.2f} votes",
                    15,
                    max_width=120,
                    anchor="rt"
                )
                y_offset += 20
                # color first place as golden, second place as silver, third place as bronze, rest as gray
                color = RANK_COLORS.get(i, (128, 128, 128))
                draw.progress_bar(
                    (x_offset + 10 + 50, y_offset),
                    (10, p_width - 30 - 50),
                    scores / proposal["scores_total"],
                    primary=color,
                )
                # show percentage next to progress bar (max 40 pixels)
                draw.dynamic_text(
                    (x_offset + 50, y_offset),
                    f"{scores / proposal['scores_total']:.0%}",
                    15,
                    max_width=45,
                    anchor="rt"
                )
                y_offset += 30
            # show how much time is left using the "end" timestamp
            d = proposal["end"] - datetime.now().timestamp()
            draw.dynamic_text(
                (x_offset + 10, y_offset),
                f"{uptime(d)} left",
                15,
                max_width=p_width - 20,
            )

        # save the image to a buffer
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        # send the image
        e.set_image(url="attachment://votes.png")
        await ctx.send(embed=e, file=File(buffer, "votes.png"))


async def setup(bot):
    await bot.add_cog(Snapshot(bot))
