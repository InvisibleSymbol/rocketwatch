import logging
import math
from abc import ABC, abstractmethod
from typing import Optional, Callable, cast

import requests
import numpy as np

from eth_typing import ChecksumAddress

from utils.cfg import cfg
from utils.retry import retry
from utils.rocketpool import rp

log = logging.getLogger("liquidity")
log.setLevel(cfg["log_level"])


class Liquidity:
    def __init__(self, price: float, depth_fn: Callable[[float], float]):
        self.price = price
        self.__depth_fn = depth_fn

    def depth_at(self, price: float) -> float:
        return self.__depth_fn(price)


class LiquiditySource(ABC):
    def __str__(self) -> str:
        return self.__class__.__name__

    @property
    @abstractmethod
    def color(self) -> str:
        pass

    @abstractmethod
    def get_liquidity(self) -> Optional[Liquidity]:
        pass


class CEX(LiquiditySource):
    def __init__(self, api_endpoint: str):
        self.api_endpoint = api_endpoint

    @retry(tries=3, delay=1)
    def get_order_book(self) -> tuple[dict[float, float], dict[float, float]]:
        response = requests.get(self.api_endpoint).json()
        bids = dict(sorted(self._get_bids(response).items()))
        asks = dict(sorted(self._get_asks(response).items()))
        return bids, asks

    @abstractmethod
    def _get_bids(self, api_response: dict) -> dict[float, float]:
        pass

    @abstractmethod
    def _get_asks(self, api_response: dict) -> dict[float, float]:
        pass

    def get_liquidity(self) -> Optional[Liquidity]:
        try:
            bids, asks = self.get_order_book()
        except Exception:
            log.exception(f"Failed to fetch order book")
            return None

        if not (bids and asks):
            log.warning(f"Empty order book")
            return None

        bid_prices = np.array(list(reversed(bids.keys())))
        bid_liquidity = np.cumsum([p * bids[p] for p in reversed(bids)])

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


class Binance(CEX):
    def __init__(self):
        super().__init__("https://api.binance.com/api/v3/depth?symbol=RPLUSDT&limit=5000")

    @property
    def color(self) -> str:
        return "#e8bd47"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["asks"]}


class Coinbase(CEX):
    def __init__(self):
        super().__init__("https://api.coinbase.com/api/v3/brokerage/market/product_book?product_id=RPL-USD")

    @property
    def color(self) -> str:
        return "#2856f5"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(bid["price"]): float(bid["size"]) for bid in api_response["pricebook"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(ask["price"]): float(ask["size"]) for ask in api_response["pricebook"]["asks"]}


class Deepcoin(CEX):
    def __init__(self):
        super().__init__("https://api.deepcoin.com/deepcoin/market/books?instId=RPL-USDT&sz=400")

    @property
    def color(self) -> str:
        return "#ee8337"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["asks"]}


class GateIO(CEX):
    def __init__(self):
        super().__init__("https://api.gateio.ws/api/v4/spot/order_book?currency_pair=RPL_USDT&limit=1000")

    @property
    def color(self) -> str:
        return "#3758de"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["asks"]}


class OKX(CEX):
    def __init__(self):
        super().__init__("https://www.okx.com/api/v5/market/books?instId=RPL-USDT&sz=400")

    @property
    def color(self) -> str:
        return "#080808"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _, _ in api_response["data"][0]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _, _ in api_response["data"][0]["asks"]}


class Bitget(CEX):
    def __init__(self):
        super().__init__("https://api.bitget.com/api/v2/spot/market/orderbook?symbol=RPLUSDT")

    @property
    def color(self) -> str:
        return "#5ac2ce"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["asks"]}


class MEXC(CEX):
    def __init__(self):
        super().__init__("https://api.mexc.com/api/v3/depth?symbol=RPLUSDT&limit=5000")

    @property
    def color(self) -> str:
        return "#0b0935"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["asks"]}


class Bybit(CEX):
    def __init__(self):
        super().__init__("https://api.bybit.com/v5/market/orderbook?category=spot&symbol=RPLUSDT&limit=200")

    @property
    def color(self) -> str:
        return "#eba93b"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["result"]["b"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["result"]["a"]}


class CryptoDotCom(CEX):
    def __init__(self):
        super().__init__("https://api.crypto.com/exchange/v1/public/get-book?instrument_name=RPL_USD")

    def __str__(self) -> str:
        return "Crypto.com"

    @property
    def color(self) -> str:
        return "#1b3376"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in api_response["result"]["data"][0]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in api_response["result"]["data"][0]["asks"]}


class Kraken(CEX):
    def __init__(self):
        super().__init__("https://api.kraken.com/0/public/Depth?pair=RPLUSD&count=500")

    @property
    def color(self) -> str:
        return "#6e3bed"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in api_response["result"]["RPLUSD"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size, _ in api_response["result"]["RPLUSD"]["asks"]}


class Kucoin(CEX):
    def __init__(self):
        super().__init__("https://api.kucoin.com/api/v1/market/orderbook/level2_100?symbol=RPL-USDT")

    @property
    def color(self) -> str:
        return "#55ae92"

    def _get_bids(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["bids"]}

    def _get_asks(self, api_response: dict) -> dict[float, float]:
        return {float(price): float(size) for price, size in api_response["data"]["asks"]}


class DEX(LiquiditySource, ABC):
    class Pool(ABC):
        @abstractmethod
        def get_liquidity(self) -> Optional[Liquidity]:
            pass

    def __init__(self, pools: list[Pool]):
        self.pools = pools

    def get_liquidity(self) -> list[Liquidity]:
        liqs = []
        for pool in self.pools:
            if liq := pool.get_liquidity():
                liqs.append(liq)
        return liqs


class Balancer(DEX):
    class WeightedPool(DEX.Pool):
        def __init__(self, balancer: 'Balancer', pool_id: str):
            self.balancer = balancer
            self.id = pool_id

        def get_liquidity(self) -> Optional[Liquidity]:
            try:
                tokens = self.balancer.vault.functions.getPoolTokens(self.id).call()
                other_balance, rpl_balance = tokens[1]
                price = other_balance / rpl_balance
            except Exception:
                log.exception("Failed to fetch token balances")
                return None

            # assume 18 digits and equal weights for now
            def depth_at(_price: float) -> float:
                constant_product = other_balance * rpl_balance
                new_other_balance = math.sqrt(_price * constant_product)
                return abs(new_other_balance - other_balance) / 1e18

            return Liquidity(price, depth_at)

    def __init__(self):
        self.vault = rp.get_contract_by_name("BalancerVault")
        super().__init__([
            Balancer.WeightedPool(self, "0x9f9d900462492d4c21e9523ca95a7cd86142f298000200000000000000000462")
        ])

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

    class Pool(DEX.Pool):
        def __init__(self, pool_address: ChecksumAddress):
            self.contract = rp.assemble_contract("UniswapV3Pool", pool_address)
            self.tick_spacing: int = self.contract.functions.tickSpacing().call()

        def tick_to_word_and_bit(self, tick: int) -> tuple[int, int]:
            compressed = int(tick // self.tick_spacing)
            if (tick < 0) and (tick % self.tick_spacing):
                compressed -= 1

            word_position = int(compressed // UniswapV3.TICK_WORD_SIZE)
            bit_position = compressed % UniswapV3.TICK_WORD_SIZE
            return word_position, bit_position

        def get_tick_net_liquidity(self, tick: int) -> int:
            return self.contract.functions.ticks(tick).call()[1]

        def get_initialized_ticks(self, current_tick: int) -> list[int]:
            ticks = []
            active_word, b = self.tick_to_word_and_bit(current_tick)

            for word in range(active_word - 5, active_word + 5):
                tick_bitmap = self.contract.functions.tickBitmap(word).call()
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

            # assume 18 decimals for now
            return float(delta_x / 1e18), float(delta_y / 1e18)

        def get_liquidity(self) -> Optional[Liquidity]:
            try:
                slot0 = self.contract.functions.slot0().call()
                price = (slot0[0] / 2 ** 96) ** 2
                calculated_tick = UniswapV3.price_to_tick(price)
                current_tick = int(calculated_tick)
                ticks = self.get_initialized_ticks(current_tick)
                initial_liquidity = self.contract.functions.liquidity().call()
            except Exception:
                log.exception("Failed to get initial liquidity information")
                return None

            if not ticks:
                log.warning("No liquidity found")
                return None

            log.debug(f"Found {len(ticks)} initialized ticks!")

            def get_cumulative_liqudity(_ticks: list[int]) -> list[float]:
                cumulative_liquidity = 0
                last_tick = calculated_tick
                active_liquidity = initial_liquidity

                liquidity = []
                for tick in _ticks:
                    if tick > last_tick:
                        other_liq, _ = self.liquidity_to_tokens(active_liquidity, last_tick, tick)
                        active_liquidity += self.get_tick_net_liquidity(tick)
                    else:
                        other_liq, _ = self.liquidity_to_tokens(active_liquidity, tick, last_tick)
                        active_liquidity -= self.get_tick_net_liquidity(tick)

                    cumulative_liquidity += other_liq
                    liquidity.append(cumulative_liquidity)
                    last_tick = tick

                return liquidity

            ask_ticks = [t for t in reversed(ticks) if t <= current_tick] + [UniswapV3.MIN_TICK]
            bid_ticks = [t for t in ticks if t > current_tick] + [UniswapV3.MAX_TICK]

            try:
                ask_liquidity = get_cumulative_liqudity(ask_ticks)
                bid_liquidity = get_cumulative_liqudity(bid_ticks)
            except Exception:
                log.exception("Failed to get tick liquidity information")
                return None

            def depth_at(_price: float) -> float:
                if _price <= 0:
                    tick = UniswapV3.MAX_TICK
                else:
                    tick = -UniswapV3.price_to_tick(_price)

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

            return Liquidity(1 / price, depth_at)

    def __init__(self):
        super().__init__([
            UniswapV3.Pool(cast(ChecksumAddress, "0xe42318eA3b998e8355a3Da364EB9D48eC725Eb45"))
        ])

    def __str__(self) -> str:
        return "Uniswap"

    @property
    def color(self) -> str:
        return "#691453"
