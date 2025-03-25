import logging
from datetime import datetime, timedelta

import pymongo
import requests
from datetime import timezone
from web3.datastructures import MutableAttributeDict as aDict

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import assemble, prepare_args
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.event import EventPlugin, Event

log = logging.getLogger("cow_orders")
log.setLevel(cfg["log_level"])


class CowOrders(EventPlugin):
    def __init__(self, bot: RocketWatch):
        super().__init__(bot, timedelta(seconds=60))
        self.state = "OK"
        self.db = pymongo.MongoClient(cfg["mongodb.uri"]).rocketwatch
        # create the cow_orders collection if it doesn't exist
        # limit the collection to 10000 entries
        # create an index on order_uid
        if "cow_orders" not in self.db.list_collection_names():
            self.db.create_collection("cow_orders", capped=True, size=10_000)
        self.collection = self.db.cow_orders
        self.collection.create_index("order_uid", unique=True)

        self.tokens = [
            str(rp.get_address_by_name("rocketTokenRPL")).lower(),
            str(rp.get_address_by_name("rocketTokenRETH")).lower()
        ]

    def _get_new_events(self) -> list[Event]:
        if self.state == "RUNNING":
            log.error("Cow Orders plugin was interrupted while running. Re-initializing...")
            self.__init__(self.bot)
        self.state = "RUNNING"
        try:
            result = self.check_for_new_events()
            self.state = "OK"
        except Exception as e:
            log.error(f"Error while checking for new Cow Orders: {e}")
            result = []
            self.state = "ERROR"
        return result

    # noinspection PyTypeChecker
    def check_for_new_events(self):
        log.info("Checking Cow Orders")
        payload = []

        # get all pending orders from the cow api (https://api.cow.fi/mainnet/api/v1/auction)

        response = requests.get("https://cow-proxy.invis.workers.dev/mainnet/api/v1/auction")
        if response.status_code != 200:
            log.error("Cow API returned non-200 status code: %s", response.text)
            raise Exception("Cow API returned non-200 status code")

        cow_orders = response.json()["orders"]

        """
         entity example:
        {
          "creationDate": "2023-01-25T04:48:02.751347Z",
          "owner": "0x40586600a136652f6d0a6cc6a62b6bd1bef7ae9a",
          "uid": "0x2f3750251ab20018addd59c7a9e57845782cdf21b9c53516dcdb9e3627ebb7e840586600a136652f6d0a6cc6a62b6bd1bef7ae9a63d9eef8",
          "availableBalance": "108475037",
          "executedBuyAmount": "0",
          "executedSellAmount": "0",
          "executedSellAmountBeforeFees": "0",
          "executedFeeAmount": "0",
          "invalidated": false,
          "status": "open",
          "class": "limit",
          "surplusFee": "10050959",
          "surplusFeeTimestamp": "2023-01-26T14:51:51.453450Z",
          "executedSurplusFee": null,
          "settlementContract": "0x9008d19f58aabd9ed0d60971565aa8510560ab41",
          "fullFeeAmount": "13254445",
          "isLiquidityOrder": false,
          "sellToken": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
          "buyToken": "0x347a96a5bd06d2e15199b032f46fb724d6c73047",
          "receiver": "0x40586600a136652f6d0a6cc6a62b6bd1bef7ae9a",
          "sellAmount": "20000000",
          "buyAmount": "17091759130902",
          "validTo": 1675226872,
          "appData": "0xc1164815465bff632c198b8455e9a421c07e8ce426c8cd1b59eef7b305b8ca90",
          "feeAmount": "0",
          "kind": "sell",
          "partiallyFillable": false,
          "sellTokenBalance": "erc20",
          "buyTokenBalance": "erc20",
          "signingScheme": "eip712",
          "signature": "0x894e427c681f1b4d24604039966321ed59993ce2a1e17fffc742c8af954aa0b10cca77ce750ce60e3d7591b60c90417d333c1d83493abafb8a36d7778e6519a51c",
          "interactions": {
            "pre": [
              
            ]
          }
        },
        """

        # filter all orders that do not contain RPL
        cow_orders = [order for order in cow_orders if order["sellToken"] in self.tokens or order["buyToken"] in self.tokens]

        # filter all orders that are not open
        cow_orders = [order for order in cow_orders if order["executed"] == "0"]

        # efficiently check if the orders are already in the database
        order_uids = [order["uid"] for order in cow_orders]
        existing_orders = self.collection.find({"order_uid": {"$in": order_uids}})
        existing_order_uids = [order["order_uid"] for order in existing_orders]

        # filter all orders that are already in the database
        cow_orders = [order for order in cow_orders if order["uid"] not in existing_order_uids]

        if not cow_orders:
            return []
        # get rpl price in dai
        rpl_ratio = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
        reth_ratio = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate"))
        rpl_price = rpl_ratio * rp.get_dai_eth_price()
        reth_price = reth_ratio * rp.get_dai_eth_price()

        # generate payloads
        for order in cow_orders:
            data = aDict({})
            token = None

            data["cow_uid"] = order["uid"]
            data["cow_owner"] = w3.toChecksumAddress(order["owner"])
            decimals = 18
            # base the event_name depending on if its buying or selling RPL
            if order["sellToken"] in self.tokens:
                token = "reth" if order["sellToken"] == self.tokens[1] else "rpl"
                data["event_name"] = f"cow_order_sell_{token}_found"
                # token/token ratio
                data["ratio"] = int(order["sellAmount"]) / int(order["buyAmount"])
                # store rpl and other token amount
                data["ourAmount"] = solidity.to_float(int(order["sellAmount"]))
                s = rp.assemble_contract(name="ERC20", address=w3.toChecksumAddress(order["buyToken"]))
                try:
                    decimals = s.functions.decimals().call()
                except:
                    pass
                data["otherAmount"] = solidity.to_float(int(order["buyAmount"]), decimals)
            else:
                token = "reth" if order["buyToken"] == self.tokens[1] else "rpl"
                data["event_name"] = f"cow_order_buy_{token}_found"
                # store rpl and other token amount
                data["ourAmount"] = solidity.to_float(int(order["buyAmount"]))
                s = rp.assemble_contract(name="ERC20", address=w3.toChecksumAddress(order["sellToken"]))
                try:
                    decimals = s.functions.decimals().call()
                except:
                    pass
                data["otherAmount"] = solidity.to_float(int(order["sellAmount"]), decimals)
            # our/other ratio
            data["ratioAmount"] = data["otherAmount"] / data["ourAmount"]
            try:
                data["otherToken"] = s.functions.symbol().call()
            except:
                data["otherToken"] = "UNKWN"
                if s.address == w3.toChecksumAddress("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"):
                    data["otherToken"] = "ETH"
            data["deadline"] = int(order["validTo"])
            # if the rpl value in usd is less than 25k, ignore it
            if data["ourAmount"] * (rpl_price if token == "rpl" else reth_price) < 25000:
                continue

            # request more data from the api
            extra = None
            try:
                t = requests.get(f"https://cow-proxy.invis.workers.dev/mainnet/api/v1/orders/{order['uid']}")
                if t.status_code != 200:
                    log.error(f"Failed to get more data from the cow api for order {order['uid']}: {t.text}")
                    continue
                extra = t.json()
            except Exception as e:
                log.error(f"Failed to get more data from the cow api for order {order['uid']}: {e}")
                continue

            if extra:
                if extra["invalidated"]:
                    log.info(f"Order {order['uid']} is invalidated, skipping")
                    continue
                created = datetime.fromisoformat(extra["creationDate"].replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - created > timedelta(minutes=15):
                    log.info(f"Order {order['uid']} is older than 15 minutes, skipping")
                    continue
                data["timestamp"] = int(created.timestamp())


            data = prepare_args(data)
            embed = assemble(data)
            payload.append(Event(
                embed=embed,
                topic="cow_orders",
                block_number=self._pending_block,
                event_name=data["event_name"],
                unique_id=f"cow_order_found_{order['uid']}"
            ))
        # dont emit if the db collection is empty - this is to prevent the bot from spamming the channel with stale data
        if not self.collection.count_documents({}):
            payload = []

        # insert all new orders into the database
        self.collection.insert_many([{"order_uid": order["uid"]} for order in cow_orders])

        log.debug("Finished Checking Cow Orders")
        return payload


async def setup(bot):
    await bot.add_cog(CowOrders(bot))
