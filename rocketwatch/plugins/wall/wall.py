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
    font_manager as fm,
    ticker,
    figure
)

from rocketwatch import RocketWatch
from utils.time_debug import timerun, timerun_async
from utils.embeds import Embed
from utils.visibility import is_hidden_weak
from utils.liquidity import *

log = logging.getLogger("wall")
log.setLevel(cfg["log_level"])


class Wall(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot
        self.cex: set[CEX] = {
            Binance("RPL", ["USDT"]),
            Coinbase("RPL", ["USDC"]),
            Deepcoin("RPL", ["USDT"]),
            GateIO("RPL", ["USDT"]),
            OKX("RPL", ["USDT"]),
            Bitget("RPL", ["USDT"]),
            MEXC("RPL", ["USDT"]),
            Bybit("RPL", ["USDT"]),
            CryptoDotCom("RPL", ["USD"]),
            Kraken("RPL", ["USD", "EUR"]),
            Kucoin("RPL", ["USDT"]),
            Bithumb("RPL", ["KRW"]),
            BingX("RPL", ["USDT"]),
            Bitvavo("RPL", ["EUR"]),
            HTX("RPL", ["USDT"]),
            BitMart("RPL", ["USDT"]),
            Bitrue("RPL", ["USDT"]),
            CoinTR("RPL", ["USDT"]),
        }
        self.dex: set[DEX] = {
            BalancerV2([
                BalancerV2.WeightedPool(HexStr("0x9f9d900462492d4c21e9523ca95a7cd86142f298000200000000000000000462"))
            ]),
            UniswapV3([
                cast(ChecksumAddress, "0xe42318eA3b998e8355a3Da364EB9D48eC725Eb45"),
                cast(ChecksumAddress, "0xcf15aD9bE9d33384B74b94D63D06B4A9Bd82f640")
            ])
        }

    @staticmethod
    def _get_market_depth_and_liquidity(
            markets: dict[Market | DEX.LiquidityPool, Liquidity],
            x: np.ndarray,
            rpl_usd: float
    ) -> tuple[np.ndarray, float]:
        depth = np.zeros_like(x)
        liquidity = 0

        for liq in markets.values():
            conv = liq.price / rpl_usd
            depth += np.array(list(map(liq.depth_at, x * conv))) / conv
            liquidity += (liq.depth_at(float(x[0] * conv)) + liq.depth_at(float(x[-1] * conv))) / conv

        return depth, liquidity

    @timerun_async
    async def _get_cex_data(self, x: np.ndarray, rpl_usd: float) -> OrderedDict[CEX, np.ndarray]:
        depth: dict[CEX, np.ndarray] = {}
        liquidity: dict[CEX, float] = {}
        async with aiohttp.ClientSession() as session:
            requests = [cex.get_liquidity(session) for cex in self.cex]
            for result in zip(self.cex, await asyncio.gather(*requests, return_exceptions=True)):
                if not isinstance(result, Exception):
                    cex, markets = result
                    depth[cex], liquidity[cex] = self._get_market_depth_and_liquidity(markets, x, rpl_usd)
                else:
                    log.error(f"Failed to get liquidity data for {cex}")
                    await self.bot.report_error(result)

        return OrderedDict(sorted(depth.items(), key=lambda e: liquidity[e[0]], reverse=True))

    @timerun
    def _get_dex_data(self, x: np.ndarray, rpl_usd: float) -> OrderedDict[DEX, np.ndarray]:
        depth: dict[DEX, np.ndarray] = {}
        liquidity: dict[DEX, float] = {}
        for dex in self.dex:
            if pools := dex.get_liquidity():
                depth[dex], liquidity[dex] = self._get_market_depth_and_liquidity(pools, x, rpl_usd)

        return OrderedDict(sorted(depth.items(), key=lambda e: liquidity[e[0]], reverse=True))

    @staticmethod
    def _label_exchange_data(
            data: OrderedDict[Exchange, np.ndarray],
            max_unique: int,
            color_other: str
    ) -> list[tuple[np.ndarray, str, str]]:
        ret = []
        for exchange, depth in list(data.items())[:max_unique]:
            ret.append((depth, str(exchange), exchange.color))

        if len(data) > max_unique:
            y = np.sum([depth for depth in list(data.values())[max_unique:]], axis=0)
            ret.append((y, "Other", color_other))

        return ret

    @staticmethod
    def _plot_data(
            x: np.ndarray,
            rpl_usd: float,
            rpl_eth: float,
            cex_data: OrderedDict[CEX, np.ndarray],
            dex_data: OrderedDict[DEX, np.ndarray],
    ) -> figure.Figure:
        fig, ax = plt.subplots(figsize=(10, 5))

        ax.minorticks_on()
        ax.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.5)
        ax.grid(True, which="minor", linestyle=":", linewidth=0.3, alpha=0.5)

        ax.set_xlabel("price")
        ax.xaxis.labelpad = 8
        ax.set_ylabel("depth")
        ax.yaxis.labelpad = 10

        y = []
        colors = []

        max_unique = 7 - min(len(dex_data), 4) if dex_data else 9
        cex_data_aggr = Wall._label_exchange_data(cex_data, max_unique, "#555555")
        max_unique = 7 - min(len(cex_data), 4) if cex_data else 9
        dex_data_aggr = Wall._label_exchange_data(dex_data, max_unique, "#777777")

        y_offset = 0.0
        max_label_length: int = np.max([len(t[1]) for t in (cex_data_aggr + dex_data_aggr)])

        def add_data(_data: list[tuple[np.ndarray, str, str]], _name: Optional[str]) -> None:
            labels, handles = [], []
            for y_values, label, color in _data:
                y.append(y_values)
                labels.append(f"{label:\u00A0<{max_label_length}}")
                colors.append(color)
                handles.append(plt.Rectangle((0, 0), 1, 1, color=color))

            nonlocal y_offset
            legend = ax.legend(
                handles,
                labels,
                title=_name,
                loc="upper left",
                bbox_to_anchor=(0, 1 - y_offset),
                prop=fm.FontProperties(family="monospace", size=10)
            )
            ax.add_artist(legend)
            y_offset += 0.025 + 0.055 * (len(_data) + int(_name is not None))

        if dex_data and cex_data:
            add_data(dex_data_aggr, "DEX")
            add_data(cex_data_aggr, "CEX")
        elif dex_data:
            add_data(dex_data_aggr, None)
        else:
            add_data(cex_data_aggr, None)

        ax.stackplot(x, np.array(y[::-1]), colors=colors[::-1], edgecolor="black", linewidth=0.3)
        ax.axvline(rpl_usd, color="black", linestyle="--", linewidth=1)

        def get_formatter(base_fmt: str, *, scale=1.0, prefix="", suffix=""):
            def formatter(_x, _pos) -> str:
                levels = [
                    (1_000_000_000, "B"),
                    (1_000_000, "M"),
                    (1_000, "K")
                ]
                modifier = ""
                base_value = _x * scale

                for m, s in levels:
                    if base_value >= round(m):
                        modifier = s
                        base_value /= m
                        break

                return prefix + f"{base_value:{base_fmt}}".rstrip(".") + modifier + suffix
            return ticker.FuncFormatter(formatter)

        range_size = x[-1] - x[0]

        x_ticks = ax.get_xticks()
        ax.set_xticks([t for t in x_ticks if abs(t - rpl_usd) >= range_size / 20] + [rpl_usd])
        ax.set_xlim((x[0], x[-1]))
        ax.xaxis.set_major_formatter(get_formatter(".2f" if (range_size >= 0.1) else ".3f", prefix="$"))
        ax.yaxis.set_major_formatter(get_formatter("#.3g", prefix="$"))

        ax_top = ax.twiny()
        ax_top.minorticks_on()
        ax_top.set_xticks([t for t in x_ticks if abs(t - rpl_usd) >= range_size / 10] + [rpl_usd])
        ax_top.set_xlim(ax.get_xlim())
        ax_top.xaxis.set_major_formatter(get_formatter(".5f", prefix="Ξ ", scale=(rpl_eth / rpl_usd)))

        ax_right = ax.twinx()
        ax_right.minorticks_on()
        ax_right.set_yticks(ax.get_yticks())
        ax_right.set_ylim(ax.get_ylim())
        ax_right.yaxis.set_major_formatter(get_formatter("#.3g", prefix="Ξ ", scale=(rpl_eth / rpl_usd)))

        return fig

    @hybrid_command()
    @describe(min_price="lower end of price range in USD")
    @describe(max_price="upper end of price range in USD")
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
                rpl_usd = list((await Binance("RPL", ["USDT"]).get_liquidity(session)).values())[0].price
                eth_usd = list((await Binance("ETH", ["USDT"]).get_liquidity(session)).values())[0].price
                rpl_eth = rpl_usd / eth_usd
        except Exception as e:
            await self.bot.report_error(e, ctx)
            return await on_fail()

        if min_price < 0:
            min_price = rpl_usd + min_price

        if max_price is None:
            max_price = 5 * rpl_usd
        elif max_price < 0:
            max_price = rpl_usd - max_price

        step_size = 0.001
        min_price = max(0.0, min(min_price, rpl_usd - 5 * step_size))
        max_price = min(100 * rpl_usd, max(max_price, rpl_usd + 5 * step_size))
        x = np.arange(min_price, max_price + step_size, step_size)

        source_desc = []
        cex_data, dex_data = {}, {}

        try:
            if sources != "CEX":
                dex_data = self._get_dex_data(x, rpl_usd)
                source_desc.append(f"{len(dex_data)} DEX")

            if sources != "DEX":
                cex_data = await self._get_cex_data(x, rpl_usd)
                source_desc.append(f"{len(cex_data)} CEX")
        except Exception as e:
            await self.bot.report_error(e, ctx)
            return await on_fail()

        if (not cex_data) and (not dex_data):
            log.error("No liquidity data found")
            return await on_fail()

        liquidity_usd = sum((y[0] + y[-1]) for y in (dex_data | cex_data).values())
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
