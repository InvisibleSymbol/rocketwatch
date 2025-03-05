import logging
from io import BytesIO

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import ticker

from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden_weak
from utils.liquidity import LiquiditySource, Liquidity, CEX, DEX

log = logging.getLogger("wall")
log.setLevel(cfg["log_level"])


class Wall(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.cex: list[CEX] = [cls() for cls in CEX.__subclasses__()]
        self.dex: list[DEX] = [cls() for cls in DEX.__subclasses__()]

    @hybrid_command()
    async def wall(self, ctx: Context):
        """Show the current RPL market depth across exchanges."""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = Embed(title="RPL Market Depth")
        embed.set_author(name="🔗 Data from CEX APIs and Ethereum Mainnet")

        cex_liquidity: dict[LiquiditySource, Liquidity] = {}
        for cex in self.cex:
            if liq := cex.get_liquidity():
                cex_liquidity[cex] = liq
            else:
                log.warning(f"Failed to fetch liquidity from {cex}")

        dex_liquidity: dict[LiquiditySource, list[Liquidity]] = {}
        for dex in self.dex:
            if liq := dex.get_liquidity():
                dex_liquidity[dex] = liq
            else:
                log.warning(f"Failed to fetch liquidity from {dex}")

        if not cex_liquidity:
            log.error("Failed to fetch any CEX liquidity data")
            embed.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
            return

        rpl_usd = float(np.mean([liq.price for liq in cex_liquidity.values()]))
        x = np.arange(0, 5 * rpl_usd, 0.01)
        y = []

        cex_depth = {}
        for cex, liq in cex_liquidity.items():
            cex_depth[cex] = np.zeros_like(x)
            for i, price in enumerate(x):
                cex_depth[cex][i] = liq.depth_at(price)

        dex_depth = {}
        for dex, liqs in dex_liquidity.items():
            dex_depth[dex] = np.zeros_like(x)
            for liq in liqs:
                conv = liq.price / rpl_usd
                for i, price in enumerate(x):
                    dex_depth[dex][i] += liq.depth_at(price * conv) / conv

        exchanges = list(sorted(cex_depth, key=lambda c: float(cex_depth[c][0] + cex_depth[c][-1]), reverse=True))
        major_cex = exchanges[:3]
        minor_cex = exchanges[3:]

        exchanges = list(sorted(dex_depth, key=lambda d: float(dex_depth[d][0] + dex_depth[d][-1]), reverse=True))
        major_dex = exchanges[:2]
        minor_dex = exchanges[2:]

        colors = []
        labels = []

        if minor_cex:
            y.append(np.sum([cex_depth[cex] for cex in minor_cex], axis=0))
            labels.append("Other CEX")
            colors.append("#555555")

        if minor_dex:
            y.append(np.sum([dex_depth[dex] for dex in minor_dex], axis=0))
            labels.append("Other DEX")
            colors.append("#777777")

        for cex in reversed(major_cex):
            y.append(cex_depth[cex])
            labels.append(str(cex))
            colors.append(cex.color)

        for dex in reversed(major_dex):
            y.append(dex_depth[dex])
            labels.append(str(dex))
            colors.append(dex.color)

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.set_facecolor("#f8f9fa")

        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)

        ax.minorticks_on()
        ax.grid(True, which='minor', linestyle=':', linewidth=0.3, alpha=0.5)

        ax.stackplot(x, np.array(y), labels=labels, colors=colors, edgecolor="black", linewidth=0.3)
        ax.axvline(rpl_usd, color="black", linestyle="--", linewidth=1)

        ax.xaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.2f}'))
        ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.0f}'))

        x_ticks = ax.get_xticks()
        x_ticks = [t for t in x_ticks if abs(t - rpl_usd) > (x[-1] - x[0]) / 20] + [rpl_usd]
        ax.set_xticks(x_ticks)

        ax.set_xlim((x[0], x[-1]))

        handles, labels = ax.get_legend_handles_labels()
        ax.legend(handles[::-1], labels[::-1], fontsize=10, title_fontsize=12, loc="upper left", labelspacing=0.5)

        img = BytesIO()
        fig.savefig(img, format="png")
        img.seek(0)
        plt.close()

        file_name = "wall.png"
        embed.set_image(url=f"attachment://{file_name}")

        embed.add_field(name="Current Price", value=f"${rpl_usd:,.2f}")
        embed.add_field(name="Liquidity Sources", value=len(self.cex) + len(self.dex))

        await ctx.send(embed=embed, files=[File(img, file_name)])
        img.close()


async def setup(bot):
    await bot.add_cog(Wall(bot))
