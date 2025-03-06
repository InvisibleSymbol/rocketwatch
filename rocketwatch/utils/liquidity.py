import math
import logging
from collections import OrderedDict
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable

import aiohttp
import numpy as np

from eth_typing import ChecksumAddress, HexStr

from utils.cfg import cfg
from utils.retry import retry_async
from utils.rocketpool import rp

log = logging.getLogger("liquidity")
log.setLevel(cfg["log_level"])


class Liquidity:
    def __init__(self, price: float, depth_fn: Callable[[float], float]):
        self.price = price
        self.__depth_fn = depth_fn

    def depth_at(self, price: float) -> float:
        return self.__depth_fn(price)

class Exchange(ABC):
    def __str__(self) -> str:
        return self.__class__.__name__

    @property
    @abstractmethod
    def color(self) -> str:
        pass


class CEX(Exchange, ABC):
    @dataclass(frozen=True, slots=True)
    class Market:
        major: str
        minor: str

    def __init__(self, pairs: list[tuple[str, str]]):
        self.markets = [CEX.Market(major.upper(), minor.upper()) for major, minor in pairs]

    @property
    @abstractmethod
    def _api_endpoint(self) -> str:
        pass

    @staticmethod
    @abstractmethod
    def _get_request_params(market: Market) -> dict[str, str | int]:
        pass

    @abstractmethod
    def _get_bids(self, api_response: dict) -> dict[float, float]:
        """Extract mapping of price to major-denominated bid liquidity from API response"""
        pass

    @abstractmethod
    def _get_asks(self, api_response: dict) -> dict[float, float]:
        """Extract mapping of price to major-denominated ask liquidity from API response"""
        pass

    @retry_async(tries=3, delay=1)
    async def _get_order_book(
            self,
            market: Market,
            session: aiohttp.ClientSession
    ) -> tuple[dict[float, float], dict[float, float]]:
        params = self._get_request_params(market)
        response = await session.get(self._api_endpoint, params=params, headers={"User-Agent": "Rocket Watch"})
        data = await response.json()
        bids = OrderedDict(sorted(self._get_bids(data).items(), reverse=True))
        asks = OrderedDict(sorted(self._get_asks(data).items()))
        return bids, asks

    async def _get_liquidity(self, market: Market, session: aiohttp.ClientSession) -> Optional[Liquidity]:
        bids, asks = await self._get_order_book(market, session)
        if not (bids and asks):
            log.warning(f"Empty order book")
            return None

        bid_prices = np.array(list(bids.keys()))
        bid_liquidity = np.cumsum([p * bids[p] for p in bids])

        ask_prices = np.array(list(asks.keys()))
        ask_liquidity = np.cumsum([p * asks[p] for p in asks])

        max_bid = float(bid_prices[0])
        min_ask = float(ask_prices[0])
        price = (max_bid + min_ask) / 2

        def depth_at(_price: float) -> float:
            if max_bid < _price < min_ask:
                return 0

            if _price <= max_bid:
                i = int(np.searchsorted(-bid_prices, -_price, "right"))
                return float(bid_liquidity[min(i, len(bid_liquidity)) - 1])
            else:
                i = int(np.searchsorted(ask_prices, _price, "right"))
                return float(ask_liquidity[min(i, len(ask_liquidity)) - 1])

        return Liquidity(price, depth_at)

    async def get_liquidity(self, session: aiohttp.ClientSession) -> dict[Market, Liquidity]:
        markets = {}
        for market in self.markets:
            if liq := await self._get_liquidity(market, session):
                markets[market] = liq
        return markets


class Binance(CEX):
    @property
    def color(self) -> str:
        return "#e8bd47"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.binance.com/api/v3/depth"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"symbol": f"{market.major}{market.minor}", "limit": 5000}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["asks"]}


class Coinbase(CEX):
    @property
    def color(self) -> str:
        return "#2856f5"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.coinbase.com/api/v3/brokerage/market/product_book"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"product_id": f"{market.major}-{market.minor}"}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(bid["price"]): float(bid["size"]) for bid in api_response["pricebook"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(ask["price"]): float(ask["size"]) for ask in api_response["pricebook"]["asks"]}


class Deepcoin(CEX):
    @property
    def color(self) -> str:
        return "#ee8337"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.deepcoin.com/deepcoin/market/books"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"instId": f"{market.major}-{market.minor}", "sz": 400}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["asks"]}


class GateIO(CEX):
    @property
    def color(self) -> str:
        return "#3758de"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.gateio.ws/api/v4/spot/order_book"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"currency_pair": f"{market.major}_{market.minor}", "limit": 1000}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["asks"]}


class OKX(CEX):
    @property
    def color(self) -> str:
        return "#080808"

    @property
    def _api_endpoint(self) -> str:
        return "https://www.okx.com/api/v5/market/books"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"instId": f"{market.major}-{market.minor}", "sz": 400}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _, _ in api_response["data"][0]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _, _ in api_response["data"][0]["asks"]}


class Bitget(CEX):
    @property
    def color(self) -> str:
        return "#5ac2ce"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.bitget.com/api/v2/spot/market/orderbook"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"symbol": f"{market.major}{market.minor}", "limit": 150}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["asks"]}


class MEXC(CEX):
    @property
    def color(self) -> str:
        return "#0b0935"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.mexc.com/api/v3/depth"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"symbol": f"{market.major}{market.minor}", "limit": 5000}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["asks"]}


class Bybit(CEX):
    @property
    def color(self) -> str:
        return "#eba93b"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.bybit.com/v5/market/orderbook"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"category": "spot", "symbol": f"{market.major}{market.minor}", "limit": 200}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["result"]["b"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["result"]["a"]}


class CryptoDotCom(CEX):
    def __str__(self) -> str:
        return "Crypto.com"

    @property
    def color(self) -> str:
        return "#1b3376"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.crypto.com/exchange/v1/public/get-book"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"instrument_name": f"{market.major}_{market.minor}", "depth": 150}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in api_response["result"]["data"][0]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in api_response["result"]["data"][0]["asks"]}


class Kraken(CEX):
    @property
    def color(self) -> str:
        return "#6e3bed"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.kraken.com/0/public/Depth"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"pair": f"{market.major}{market.minor}", "count": 500}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in list(api_response["result"].values())[0]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in list(api_response["result"].values())[0]["asks"]}


class Kucoin(CEX):
    @property
    def color(self) -> str:
        return "#55ae92"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.kucoin.com/api/v1/market/orderbook/level2_100"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"symbol": f"{market.major}-{market.minor}"}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["asks"]}


class Bithumb(CEX):
    @property
    def color(self) -> str:
        return "#ea7825"

    @property
    def _api_endpoint(self) -> str:
        return "https://api.bithumb.com/v1/orderbook"

    @staticmethod
    def _get_request_params(market: CEX.Market) -> dict[str, str | int]:
        return {"markets": f"{market.minor}-{market.major}"}

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {entry["bid_price"]: entry["bid_size"] for entry in api_response[0]["orderbook_units"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {entry["ask_price"]: entry["ask_size"] for entry in api_response[0]["orderbook_units"]}


class ERC20Token:
    def __init__(self, address: ChecksumAddress):
        self.address = address
        contract = rp.assemble_contract("ERC20", address)
        self.symbol: str = contract.functions.symbol().call()
        self.decimals: int = contract.functions.decimals().call()

    def __str__(self) -> str:
        return self.symbol

    def __repr__(self) -> str:
        return f"{self.symbol} ({self.address})"


class DEX(Exchange, ABC):
    class LiquidityPool(ABC):
        @abstractmethod
        def get_liquidity(self) -> Optional[Liquidity]:
            pass

    def __init__(self, pools: list[LiquidityPool]):
        self.pools = pools

    def get_liquidity(self) -> dict[LiquidityPool, Liquidity]:
        pools = {}
        for pool in self.pools:
            if liq := pool.get_liquidity():
                pools[pool] = liq
        return pools


class BalancerV2(DEX):
    class WeightedPool(DEX.LiquidityPool):
        def __init__(self, pool_id: HexStr):
            self.vault = rp.get_contract_by_name("BalancerVault")
            self.id = pool_id
            tokens = self.vault.functions.getPoolTokens(self.id).call()[0]
            self.token_0 = ERC20Token(tokens[0])
            self.token_1 = ERC20Token(tokens[1])

        def get_liquidity(self) -> Optional[Liquidity]:
            balance_0, balance_1 = self.vault.functions.getPoolTokens(self.id).call()[1]
            if (balance_0 == 0) or (balance_1 == 0):
                log.warning("Empty token balances")
                return None

            balance_norm = 10 ** (self.token_1.decimals - self.token_0.decimals)
            price = balance_norm * balance_0 / balance_1

            # assume equal weights and liquidity in token 0 for now
            def depth_at(_price: float) -> float:
                invariant = balance_0 * balance_1
                new_balance_0 = math.sqrt(_price * invariant / balance_norm)
                return abs(new_balance_0 - balance_0) / (10 ** self.token_0.decimals)

            return Liquidity(price, depth_at)

    def __init__(self, pools: list[WeightedPool]):
        # missing support for other pool types
        super().__init__(pools)

    def __str__(self):
        return "Balancer"

    @property
    def color(self) -> str:
        return "#c0c0c0"


class UniswapV3(DEX):
    TICK_WORD_SIZE = 256
    MIN_TICK = -887_272
    MAX_TICK = 887_272

    @staticmethod
    def tick_to_price(tick: int) -> float:
        return 1.0001 ** tick

    @staticmethod
    def price_to_tick(price: float) -> float:
        return math.log(price, 1.0001)

    class Pool(DEX.LiquidityPool):
        def __init__(self, pool_address: ChecksumAddress):
            self.contract = rp.assemble_contract("UniswapV3Pool", pool_address)
            self.tick_spacing: int = self.contract.functions.tickSpacing().call()
            self.token_0 = ERC20Token(self.contract.functions.token0().call())
            self.token_1 = ERC20Token(self.contract.functions.token1().call())

        def tick_to_word_and_bit(self, tick: int) -> tuple[int, int]:
            compressed = int(tick // self.tick_spacing)
            if (tick < 0) and (tick % self.tick_spacing):
                compressed -= 1

            word_position = int(compressed // UniswapV3.TICK_WORD_SIZE)
            bit_position = compressed % UniswapV3.TICK_WORD_SIZE
            return word_position, bit_position

        def get_ticks_net_liquidity(self, ticks: list[int]) -> dict[int, int]:
            return dict(zip(ticks, [
                res.results[1] for res in rp.multicall.aggregate(
                    [self.contract.functions.ticks(tick) for tick in ticks],
                ).results
            ]))

        def get_initialized_ticks(self, current_tick: int) -> list[int]:
            ticks = []
            active_word, b = self.tick_to_word_and_bit(current_tick)

            word_range = list(range(active_word - 5, active_word + 5))
            bitmaps = [
                res.results[0] for res in rp.multicall.aggregate(
                    [self.contract.functions.tickBitmap(word) for word in word_range],
                ).results
            ]

            for word, tick_bitmap in zip(word_range, bitmaps):
                if not tick_bitmap:
                    continue

                for b in range(UniswapV3.TICK_WORD_SIZE):
                    if (tick_bitmap >> b) & 1:
                        tick = (word * UniswapV3.TICK_WORD_SIZE + b) * self.tick_spacing
                        ticks.append(tick)

            return ticks

        def liquidity_to_tokens(self, liquidity: int, tick_lower: int, tick_upper: int) -> tuple[float, float]:
            sqrtp_lower = math.sqrt(UniswapV3.tick_to_price(tick_lower))
            sqrtp_upper = math.sqrt(UniswapV3.tick_to_price(tick_upper))

            delta_x = (1 / sqrtp_lower - 1 / sqrtp_upper) * liquidity
            delta_y = (sqrtp_upper - sqrtp_lower) * liquidity

            balance_0 = float(delta_x / (10 ** self.token_0.decimals))
            balance_1 = float(delta_y / (10 ** self.token_1.decimals))

            return balance_0, balance_1

        def get_liquidity(self) -> Optional[Liquidity]:
            sqrt96x = self.contract.functions.slot0().call()[0]
            initial_liquidity = self.contract.functions.liquidity().call()

            price = (sqrt96x / 2**96) ** 2
            calculated_tick = UniswapV3.price_to_tick(price)
            current_tick = int(calculated_tick)
            ticks = self.get_initialized_ticks(current_tick)

            if not ticks:
                log.warning("No liquidity found")
                return None

            log.debug(f"Found {len(ticks)} initialized ticks!")

            def get_cumulative_liquidity(_ticks: list[int]) -> list[float]:
                cumulative_liquidity = 0
                last_tick = calculated_tick
                active_liquidity = initial_liquidity

                net_liquidity: dict[int, int] = self.get_ticks_net_liquidity(_ticks)
                liquidity = []

                # assume liquidity in token 0 for now
                for tick in _ticks:
                    if tick > last_tick:
                        liq_0, _ = self.liquidity_to_tokens(active_liquidity, last_tick, tick)
                        active_liquidity += net_liquidity[tick]
                    else:
                        liq_0, _ = self.liquidity_to_tokens(active_liquidity, tick, last_tick)
                        active_liquidity -= net_liquidity[tick]

                    cumulative_liquidity += liq_0
                    liquidity.append(cumulative_liquidity)
                    last_tick = tick

                return liquidity

            ask_ticks = [t for t in reversed(ticks) if t <= current_tick] + [UniswapV3.MIN_TICK]
            ask_liquidity = get_cumulative_liquidity(ask_ticks)

            bid_ticks = [t for t in ticks if t > current_tick] + [UniswapV3.MAX_TICK]
            bid_liquidity = get_cumulative_liquidity(bid_ticks)

            balance_norm = 10 ** (self.token_1.decimals - self.token_0.decimals)

            def depth_at(_price: float) -> float:
                if _price <= 0:
                    tick = UniswapV3.MAX_TICK
                else:
                    tick = -UniswapV3.price_to_tick(_price / balance_norm)

                if tick <= calculated_tick:
                    i = int(np.searchsorted(-np.array(ask_ticks), -tick))
                    liq_ticks = ask_ticks
                    liquidity_levels = ask_liquidity
                else:
                    i = int(np.searchsorted(np.array(bid_ticks), tick))
                    liq_ticks = bid_ticks
                    liquidity_levels = bid_liquidity

                if i <= 0:
                    return 0
                elif i >= len(liquidity_levels):
                    return liquidity_levels[-1]

                range_share = abs(tick - liq_ticks[i - 1]) / abs(liq_ticks[i] - liq_ticks[i - 1])
                range_liquidity = abs(liquidity_levels[i] - liquidity_levels[i - 1])
                return float(liquidity_levels[i - 1] + range_share * range_liquidity)

            return Liquidity(balance_norm / price, depth_at)

    def __init__(self, pools: list[ChecksumAddress]):
        super().__init__([UniswapV3.Pool(pool) for pool in pools])

    def __str__(self) -> str:
        return "Uniswap"

    @property
    def color(self) -> str:
        return "#691453"
