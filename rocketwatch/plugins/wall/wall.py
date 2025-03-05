import asyncio
from io import BytesIO
from typing import cast, Literal

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
    def _get_cex_data(liquidity: dict[CEX, Liquidity], x: np.ndarray, max_unique: int) -> list[tuple[np.ndarray, str, str]]:
        depth: dict[CEX, np.ndarray] = {}
        for cex, liq in liquidity.items():
            depth[cex] = np.array(list(map(liq.depth_at, x)))

        def get_range_liquidity(_e: CEX) -> float:
            return liquidity[_e].depth_at(float(x[0])) + liquidity[_e].depth_at(float(x[-1]))

        exchanges = list(sorted(depth, key=get_range_liquidity, reverse=True))
        ret = []

        for exchange in exchanges[:max_unique]:
            ret.append((depth[exchange], str(exchange), exchange.color))

        if len(exchanges) > max_unique:
            y = np.sum([depth[cex] for cex in exchanges[max_unique:]], axis=0)
            ret.append((y, "Other", "#555555"))

        return ret

    def _get_dex_data(self, x: np.ndarray, rpl_usd: float, max_unique: int) -> list[tuple[np.ndarray, str, str]]:
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

        for exchange in exchanges[:max_unique]:
            ret.append((depth[exchange], str(exchange), exchange.color))

        if len(exchanges) > max_unique:
            y = np.sum([depth[cex] for cex in exchanges[max_unique:]], axis=0)
            ret.append((y, "Other", "#777777"))

        return ret

    @staticmethod
    def _plot_data(
            x: np.ndarray,
            rpl_usd: float,
            cex_data: list[tuple[np.ndarray, str, str]],
            dex_data: list[tuple[np.ndarray, str, str]]
    ) -> figure.Figure:
        y = []
        colors = []

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.set_facecolor("#f8f9fa")
        ax.set_title("RPL Market Depth", fontsize=14, fontweight='bold')

        ax.minorticks_on()
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
        ax.grid(True, which='minor', linestyle=':', linewidth=0.3, alpha=0.5)

        ax.axvline(rpl_usd, color="black", linestyle="--", linewidth=1)

        ax.xaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.2f}'))
        ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.0f}'))

        y_offset = 0

        def add_data(_data: list[tuple[np.ndarray, str, str]], _legend_title: Optional[str]) -> None:
            labels, handles = [], []
            for y_values, label, color in _data:
                y.append(y_values)
                labels.append(label)
                colors.append(color)
                handles.append(plt.Rectangle((0, 0), 1, 1, color=color))

            legend = ax.legend(
                handles,
                labels,
                title=_legend_title,
                fontsize=10,
                title_fontsize=10,
                loc="upper left",
                bbox_to_anchor=(0, 1 - y_offset)
            )
            ax.add_artist(legend)

        if dex_data:
            title = "DEX" if cex_data else None
            add_data(dex_data, title)
            y_offset += 0.085 + 0.055 * len(dex_data)

        if cex_data:
            title = "CEX" if dex_data else None
            add_data(cex_data, title)

        ax.stackplot(x, np.array(y[::-1]), colors=colors[::-1], edgecolor="black", linewidth=0.3)

        x_ticks = ax.get_xticks()
        x_ticks = [t for t in x_ticks if abs(t - rpl_usd) > (x[-1] - x[0]) / 20] + [rpl_usd]
        ax.set_xticks(x_ticks)
        ax.set_xlim((x[0], x[-1]))

        return fig

    @hybrid_command()
    async def wall(self, ctx: Context, exchanges: Literal["All", "CEX", "DEX"] = "All"):
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

        sources = []
        cex_data, dex_data = [], []
        total_liquidity = 0

        if exchanges != "CEX":
            max_unique = 5 if (exchanges == "DEX") else 2
            dex_data = self._get_dex_data(x, rpl_usd, max_unique)
            sources.append(f"{len(self.dex)} DEX")
            total_liquidity += sum(y[0] + y[-1] for y, _, _ in dex_data)

        if exchanges != "DEX":
            max_unique = 5 if (exchanges == "CEX") else 3
            cex_data = self._get_cex_data(cex_liquidity, x, max_unique)
            sources.append(f"{len(self.cex)} CEX")
            total_liquidity += sum(y[0] + y[-1] for y, _, _ in cex_data)

        embed.add_field(name="Current Price", value=f"${rpl_usd:,.2f}")
        embed.add_field(name="Observed Liquidity", value=f"${total_liquidity:,.0f}")
        embed.add_field(name="Sources", value=", ".join(sources))

        buffer = BytesIO()
        fig = self._plot_data(x, rpl_usd, cex_data, dex_data)
        fig.savefig(buffer, format="png")
        buffer.seek(0)

        file_name = "wall.png"
        embed.set_image(url=f"attachment://{file_name}")
        await ctx.send(embed=embed, files=[File(buffer, file_name)])


async def setup(bot):
    await bot.add_cog(Wall(bot))
