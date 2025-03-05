import asyncio
from io import BytesIO
from typing import cast

from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from matplotlib import (
    pyplot as plt,
    ticker,
    figure
)

from rocketwatch import RocketWatch
from utils.embeds import Embed
from utils.visibility import is_hidden_weak
from utils.liquidity import *

log = logging.getLogger("wall")
log.setLevel(cfg["log_level"])


class Wall(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.cex: list[CEX] = [
            Binance("RPL", "USDT"),
            Coinbase("RPL", "USD"),
            Deepcoin("RPL", "USDT"),
            GateIO("RPL", "USDT"),
            OKX("RPL", "USDT"),
            Bitget("RPL", "USDT"),
            MEXC("RPL", "USDT"),
            Bybit("RPL", "USDT"),
            CryptoDotCom("RPL", "USD"),
            Kraken("RPL", "USD"),
            Kucoin("RPL", "USDT")
        ]
        self.dex: list[DEX] = [
            BalancerV2([
                BalancerV2.WeightedPool(HexStr("0x9f9d900462492d4c21e9523ca95a7cd86142f298000200000000000000000462"))
            ]),
            UniswapV3([
                cast(ChecksumAddress, "0xe42318eA3b998e8355a3Da364EB9D48eC725Eb45")
            ])
        ]

    @staticmethod
    def _get_cex_data(liquidity: dict[CEX, Liquidity], x: np.ndarray) -> list[tuple[np.ndarray, str, str]]:
        depth: dict[CEX, np.ndarray] = {}
        for cex, liq in liquidity.items():
            depth[cex] = np.array(list(map(liq.depth_at, x)))

        def get_range_liquidity(_e: CEX) -> float:
            return liquidity[_e].depth_at(float(x[0])) + liquidity[_e].depth_at(float(x[-1]))

        exchanges = list(sorted(depth, key=get_range_liquidity, reverse=True))
        ret = []

        if len(exchanges) > 3:
            y = np.sum([depth[cex] for cex in exchanges[3:]], axis=0)
            ret.append((y, "Other", "#555555"))

        for exchange in reversed(exchanges[:3]):
            ret.append((depth[exchange], str(exchange), exchange.color))

        return ret

    def _get_dex_data(self, x: np.ndarray, rpl_usd: float) -> list[tuple[np.ndarray, str, str]]:
        depth: dict[DEX, np.ndarray] = {}
        liquidity: dict[DEX, float] = {}
        for dex in self.dex:
            if not (pools := dex.get_liquidity()):
                log.warning(f"Failed to fetch liquidity from {dex}")
                continue

            depth[dex] = np.zeros_like(x)
            liquidity[dex] = 0

            for liq in pools:
                conv = liq.price / rpl_usd
                depth[dex] += np.array(list(map(liq.depth_at, x * conv))) / conv
                liquidity[dex] += liq.depth_at(float(x[0])) + liq.depth_at(float(x[-1]))

        def get_range_liquidity(_e: DEX) -> float:
            return liquidity[_e]

        exchanges = list(sorted(depth, key=get_range_liquidity, reverse=True))
        ret = []

        if len(exchanges) > 2:
            y = np.sum([depth[cex] for cex in exchanges[2:]], axis=0)
            ret.append((y, "Other", "#777777"))

        for exchange in reversed(exchanges[:2]):
            ret.append((depth[exchange], str(exchange), exchange.color))

        return ret

    @staticmethod
    def _plot_data(x, rpl_usd, cex_data, dex_data) -> figure.Figure:
        cex_labels, cex_colors = [], []
        y = []

        for y_values, label, color in cex_data:
            y.append(y_values)
            cex_labels.append(label)
            cex_colors.append(color)

        dex_labels, dex_colors = [], []
        for y_values, label, color in dex_data:
            y.append(y_values)
            dex_labels.append(label)
            dex_colors.append(color)

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.set_facecolor("#f8f9fa")

        ax.set_title("RPL Market Depth", fontsize=14, fontweight='bold')

        ax.minorticks_on()
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
        ax.grid(True, which='minor', linestyle=':', linewidth=0.3, alpha=0.5)

        ax.stackplot(x, np.array(y), colors=(cex_colors + dex_colors), edgecolor="black", linewidth=0.3)
        ax.axvline(rpl_usd, color="black", linestyle="--", linewidth=1)

        ax.xaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.2f}'))
        ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.0f}'))

        x_ticks = ax.get_xticks()
        x_ticks = [t for t in x_ticks if abs(t - rpl_usd) > (x[-1] - x[0]) / 20] + [rpl_usd]
        ax.set_xticks(x_ticks)

        ax.set_xlim((x[0], x[-1]))

        handles = [plt.Rectangle((0, 0), 1, 1, color=color) for color in dex_colors]
        legend = ax.legend(
            handles[::-1],
            dex_labels[::-1],
            title="DEX",
            fontsize=10,
            title_fontsize=10,
            loc="upper left",
            bbox_to_anchor=(0, 1)
        )
        ax.add_artist(legend)

        y_offset = 0.1 + 0.055 * len(handles)

        handles = [plt.Rectangle((0, 0), 1, 1, color=color) for color in cex_colors]
        legend = ax.legend(
            handles[::-1],
            cex_labels[::-1],
            title="CEX",
            fontsize=10,
            title_fontsize=10,
            loc="upper left",
            bbox_to_anchor=(0, 1 - y_offset)
        )
        ax.add_artist(legend)

        return fig

    @hybrid_command()
    async def wall(self, ctx: Context):
        """Show the current RPL market depth across exchanges."""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = Embed()
        embed.set_author(name="🔗 Data from CEX APIs and Ethereum Mainnet")

        cex_liquidity: dict[CEX, Liquidity] = {}
        async with aiohttp.ClientSession() as session:
            requests = [cex.get_liquidity(session) for cex in self.cex]
            for cex, liq in zip(self.cex, await asyncio.gather(*requests)):
                if not liq:
                    log.warning(f"Failed to fetch liquidity from {cex}")
                    continue

                # only looking at one pair for now (RPL / USD-like)
                cex_liquidity[cex] = liq

        if not cex_liquidity:
            log.error("Failed to fetch any CEX liquidity data")
            embed.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
            return

        rpl_usd = float(np.mean([liq.price for liq in cex_liquidity.values()]))
        x = np.arange(0, 5 * rpl_usd, 0.01)

        cex_data = self._get_cex_data(cex_liquidity, x)
        dex_data = self._get_dex_data(x, rpl_usd)
        fig = self._plot_data(x, rpl_usd, cex_data, dex_data)

        embed.add_field(name="Current Price", value=f"${rpl_usd:,.2f}")
        embed.add_field(name="Liquidity Sources", value=f"{len(self.cex)} CEX, {len(self.dex)} DEX")

        buffer = BytesIO()
        fig.savefig(buffer, format="png")
        buffer.seek(0)

        file_name = "wall.png"
        embed.set_image(url=f"attachment://{file_name}")
        await ctx.send(embed=embed, files=[File(buffer, file_name)])

async def setup(bot):
    await bot.add_cog(Wall(bot))
