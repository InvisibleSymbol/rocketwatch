import asyncio
from io import BytesIO
from typing import cast, Literal

from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from discord.app_commands import describe
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

    async def _get_cex_data(self, x: np.ndarray, rpl_usd: float, max_unique: int) -> list[tuple[np.ndarray, str, str]]:
        depth: dict[CEX, np.ndarray] = {}
        liquidity: dict[CEX, float] = {}
        async with aiohttp.ClientSession() as session:
            requests = [cex.get_liquidity(session) for cex in self.cex]
            for cex, liq in zip(self.cex, await asyncio.gather(*requests)):
                if not liq:
                    log.warning(f"Failed to fetch liquidity from {cex}")
                    continue

                # only look at one pair for now
                conv = liq.price / rpl_usd
                depth[cex] = np.array(list(map(liq.depth_at, x * conv))) / conv
                liquidity[cex] = liq.depth_at(float(x[0])) + liq.depth_at(float(x[-1]))

        exchanges = list(sorted(depth, key=liquidity.get, reverse=True))
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

        exchanges = list(sorted(depth, key=liquidity.get, reverse=True))
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
            rpl_eth: float,
            cex_data: list[tuple[np.ndarray, str, str]],
            dex_data: list[tuple[np.ndarray, str, str]]
    ) -> figure.Figure:
        y = []
        colors = []

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.set_facecolor("#f8f9fa")

        ax.minorticks_on()
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
        ax.grid(True, which='minor', linestyle=':', linewidth=0.3, alpha=0.5)

        ax.axvline(rpl_usd, color="black", linestyle="--", linewidth=1)

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

        def get_formatter(base_fmt: str, *, scale=1.0, prefix="", suffix=""):
            def formatter(_x, _pos) -> str:
                levels = [
                    (1_000_000_000, "B"),
                    (1_000_000, "M"),
                    (1_000, "K")
                ]
                modifier = ""
                base_value = _x * scale
                log.info(f"{base_value = }")

                for m, s in levels:
                    if base_value >= round(m):
                        modifier = s
                        base_value /= m
                        break

                return prefix + f"{base_value:{base_fmt}}".rstrip(".") + modifier + suffix
            return ticker.FuncFormatter(formatter)

        x_ticks = ax.get_xticks()
        ax.set_xticks([t for t in x_ticks if abs(t - rpl_usd) >= (x[-1] - x[0]) / 15] + [rpl_usd])
        ax.set_xlim((x[0], x[-1]))
        ax.xaxis.set_major_formatter(get_formatter(".2f", prefix="$"))
        ax.yaxis.set_major_formatter(get_formatter("#.3g", prefix="$"))

        ax_top = ax.twiny()
        ax_top.minorticks_on()
        ax_top.set_xticks([t for t in x_ticks if abs(t - rpl_usd) >= (x[-1] - x[0]) / 10] + [rpl_usd])
        ax_top.set_xlim(ax.get_xlim())
        ax_top.xaxis.set_major_formatter(get_formatter(".5f", prefix="Ξ ", scale=(rpl_eth / rpl_usd)))

        ax_right = ax.twinx()
        ax_right.minorticks_on()
        ax_right.set_yticks(ax.get_yticks())
        ax_right.set_ylim(ax.get_ylim())
        ax_right.yaxis.set_major_formatter(get_formatter("#.3g", prefix="Ξ ", scale=(rpl_eth / rpl_usd)))

        return fig

    @hybrid_command()
    @describe(min_price="lower end of price range to show in USD")
    @describe(max_price="upper end of price range to show in USD")
    @describe(sources="choose places to pull liquidity data from")
    async def wall(
            self,
            ctx: Context,
            min_price: float = 0.0,
            max_price: float = None,
            sources: Literal["All", "CEX", "DEX"] = "All"
    ) -> None:
        """Show the current RPL market depth across exchanges"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = Embed(title="RPL Market Depth")

        async def on_fail() -> None:
            embed.set_image(url="https://media1.giphy.com/media/hEc4k5pN17GZq/giphy.gif")
            await ctx.send(embed=embed)
            return None

        try:
            async with aiohttp.ClientSession() as session:
                # use Binance as price oracle
                rpl_usd = (await Binance("RPL", "USDT").get_liquidity(session)).price
                eth_usd = (await Binance("ETH", "USDT").get_liquidity(session)).price
                rpl_eth = rpl_usd / eth_usd
        except Exception as e:
            await self.bot.report_error(e, ctx)
            return await on_fail()

        source_desc = []
        cex_data, dex_data = [], []
        liquidity_usd = 0

        step_size = 0.01
        min_price = max(0.0, min(min_price, rpl_usd - 5 * step_size))
        if max_price is None:
            max_price = 5 * rpl_usd
        else:
            max_price = min(100 * rpl_usd, max(max_price, rpl_usd + 5 * step_size))

        x = np.arange(min_price, max_price, step_size)

        try:
            if sources != "CEX":
                max_unique = 3 if (sources == "All") else 7
                dex_data = self._get_dex_data(x, rpl_usd, max_unique)
                source_desc.append(f"{len(self.dex)} DEX")
                liquidity_usd += sum(y[0] + y[-1] for y, _, _ in dex_data)

            if sources != "DEX":
                max_unique = 3 if (sources == "All") else 7
                cex_data = await self._get_cex_data(x, rpl_usd, max_unique)
                source_desc.append(f"{len(self.cex)} CEX")
                liquidity_usd += sum(y[0] + y[-1] for y, _, _ in cex_data)
        except Exception as e:
            await self.bot.report_error(e, ctx)
            return await on_fail()

        if (not cex_data) and (not dex_data):
            log.error("No liquidity data found")
            return await on_fail()

        liquidity_eth = liquidity_usd / eth_usd

        buffer = BytesIO()
        fig = self._plot_data(x, rpl_usd, rpl_eth, cex_data, dex_data)
        fig.savefig(buffer, format="png")
        buffer.seek(0)

        embed.set_author(name="🔗 Data from CEX APIs and Mainnet")
        embed.add_field(name="Current Price", value=f"${rpl_usd:,.2f} | Ξ{rpl_eth:.5f}")
        embed.add_field(name="Observed Liquidity", value=f"${liquidity_usd:,.0f} | Ξ{liquidity_eth:,.0f}")
        embed.add_field(name="Sources", value=", ".join(source_desc))

        file_name = "wall.png"
        embed.set_image(url=f"attachment://{file_name}")
        await ctx.send(embed=embed, files=[File(buffer, file_name)])
        return None


async def setup(bot):
    await bot.add_cog(Wall(bot))
