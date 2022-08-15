import asyncio
import base64
import json
import logging
import time
import urllib
from datetime import datetime
from datetime import timezone
from urllib.parse import urlencode

import aiohttp
import discord
from discord import app_commands, Interaction, ButtonStyle
from discord import ui
from discord.ext import commands, tasks
from eth_account.messages import encode_defunct
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from utils.cfg import cfg
from utils.embeds import Embed
from utils.shared_w3 import w3

log = logging.getLogger("poap")
log.setLevel(cfg["log_level"])


# Define a simple View that gives us a counter button
class EnableRequest(ui.View):
    def __init__(self, user: discord.User, db: AsyncIOMotorClient):
        super().__init__()
        payload = {
            "username" : str(user),
            "user_id"  : user.id,
            "timestamp": int(time.time()),
            "comment"  : "Rocketwatch: Enable automatic claiming of POAPs"
        }
        url = "https://signer.is/#/sign/"
        payload = json.dumps(payload)
        payload = {"requested_message": payload}
        payload = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
        url = f"{url}{payload}"

        self.db = db
        self.add_item(ui.Button(label='Sign Message', url=url))

    @ui.button(label='Submit Signature URL', style=ButtonStyle.green, row=1)
    async def sign(self, interaction: Interaction, button: ui.Button):
        # Make sure to update the message with our updated selves
        await interaction.response.send_modal(PoapSignatureModal(interaction, self.db))


class PoapSignatureModal(ui.Modal, title='Enable POAP Automatic Claim'):
    def __init__(self, interaction: Interaction, db: AsyncIOMotorClient):
        super().__init__()
        self.orig_interaction = interaction
        self.text_field = ui.TextInput(label='Signature URL', placeholder='https://signer.is/#/verify/...', min_length=740,
                                       max_length=750)
        self.add_item(self.text_field)
        self.db = db

    async def on_submit(self, interaction: Interaction):
        # decode the url we received
        try:
            url = self.text_field.value
            res = urllib.parse.unquote(base64.b64decode(url.split("/")[-1]))
            res = json.loads(res)
            payload = json.loads(res["claimed_message"])

            assert payload["user_id"] == interaction.user.id, "Incorrect user in signature"
            assert payload["comment"] == "Rocketwatch: Enable automatic claiming of POAPs", "Incorrect comment in signature"
            # timestamp has to be within the last 15 minutes
            assert abs(
                payload["timestamp"] - int(time.time())) < 9000, "Timestamp is too old"  # TODO revert back to 900 after testing

            # verify the signature
            recov = w3.eth.account.recover_message(
                encode_defunct(text=res["claimed_message"]),
                signature=res["signed_message"])
            assert recov == w3.toChecksumAddress(res["claimed_signatory"])
            # sort by delivery_id
            last_delivery_id = await self.db.poap_deliveries.find_one(sort=[("delivery_id", -1)])
            if last_delivery_id is None:
                last_delivery_id = 0
            else:
                last_delivery_id = last_delivery_id["delivery_id"]
            await self.db.poap_users.update_one(
                {"user_id": interaction.user.id},
                {"$set": {"address": str(recov), "last_delivery_id": last_delivery_id, 'last_updated': datetime.utcnow()}},
                upsert=True)
            e = Embed()
            e.title = "Successfully Enabled POAP Automatic Claim"
            e.description = "Your POAPs will now automatically be claimed for you!"
            await interaction.response.edit_message(embed=e, view=None)
        except Exception as e:
            await self.on_error(interaction, e)
            return

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        e = Embed()
        e.title = "An error occurred"
        if isinstance(error, AssertionError):
            e.description = f"{error}"
        else:
            e.description = "Please try again"
        log.exception(error)
        await interaction.response.edit_message(embed=e, view=None)


class Poap(commands.GroupCog, name="poap-autoclaim"):
    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.cached_commands = None
        self.session_headers = {
            "user-agent"  : "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36",
            "accept"      : "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "refferer"    : "https://poap.delivery/",
            "content-type": "application/json",
        }

        if not self.run_loop.is_running() and bot.is_ready():
            self.run_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.run_loop.is_running():
            return
        self.run_loop.start()

    async def check_db_indexes(self):
        try:
            # create an index for address, an index for delivery_id, and an unique index for the combination of address and delivery_id
            await self.db.poap_deliveries.create_index("address", background=True)
            await self.db.poap_deliveries.create_index("last_delivery_id", background=True)
            await self.db.poap_deliveries.create_index([("address", 1), ("last_delivery_id", 1)], unique=True, background=True)
        except Exception as e:
            log.exception(e)

    @tasks.loop(seconds=60 * 5)
    async def run_loop(self):
        try:
            await self.check_db_indexes()
            await self.sync_possible_deliveries_to_db()
            await self.process_next_user()
        except Exception as e:
            log.exception(e)

    async def mention_command(self, name):
        if not self.cached_commands:
            self.cached_commands = await self.bot.tree.fetch_commands()
        for c in self.cached_commands:
            if c.options:
                for subc in c.options:
                    if subc.name == name:
                        return subc.mention
        return f"/poap-autoclaim {name}"

    async def sync_possible_deliveries_to_db(self):
        log.info("Syncing possible deliveries to db")
        # https://poap.delivery/page-data/index/page-data.json returns all possible deliveries
        async with aiohttp.ClientSession(
                headers=self.session_headers) as session:
            async with session.get("https://poap.delivery/page-data/index/page-data.json") as resp:
                data = await resp.json()
            target_hash = data["staticQueryHashes"][0]
            db_delivery_ids = await self.db.poap_deliveries.distinct("delivery_id")
            web_ids = []
            async with session.get(f"https://poap.delivery/page-data/sq/d/{target_hash}.json") as resp:
                data = await resp.json()
                for delivery in data["data"]["deliveries"]["list"]:
                    delivery_id = delivery["id"]
                    web_ids.append(delivery_id)
                    if delivery_id in db_delivery_ids:
                        continue
                    log.info(f"Processing delivery {delivery_id}")
                    # requests the addresses from the server
                    addresses = []
                    while True:
                        offset = len(addresses)
                        url = f"https://api.poap.tech/delivery-addresses/{delivery_id}?limit=1000&offset={offset}"
                        async with session.get(url) as resp:
                            data = await resp.json()
                            if not data["items"]:
                                break
                            addresses.extend(
                                UpdateOne(
                                    {"delivery_id": delivery_id, "address": item["address"]},
                                    {"$set": {"address": item["address"]}},
                                    upsert=True
                                ) for item in data["items"])
                            if len(addresses) >= data["total"]:
                                break
                            await asyncio.sleep(1)
                    # insert the addresses into the database
                    await self.db.poap_deliveries.bulk_write(addresses)
                    await asyncio.sleep(2)
            # remove deliveries that are no longer available
            await self.db.poap_deliveries.delete_many({"delivery_id": {"$nin": web_ids}})
        log.info("Done syncing possible deliveries to db")

    @app_commands.command()
    async def enable(self,
                     inter: Interaction):
        """
        Enable automatic claiming of POAPs.
        """
        e = Embed()
        user = await self.db.poap_users.find_one({"user_id": inter.user.id})
        if user is not None:
            e.title = "POAP Automatic Claim Already Enabled"
            e.description = "You have already enabled automatic claiming of POAPs.\n" \
                            f"Your current address is: `{user['address']}`.\n" \
                            f"If you would like to change your address, " \
                            f"please use the {await self.mention_command('disable')} command."
            await inter.response.send_message(embed=e, view=None, ephemeral=True)
            return

        e.title = "Enable POAP Automatic Claim"
        e.description = f"Please click the **\"Sign Message\"** button to sign the message below.\n\n" \
                        f"After you have signed the message in your browser, press **\"Copy Link\"** " \
                        f"and continue with the **\"Submit Signature URL\"** button."
        e.set_image(url="https://i.imgur.com/Z325o4h.png")

        await inter.response.send_message(embed=e, view=EnableRequest(inter.user, self.db), ephemeral=True)

    @app_commands.command()
    async def disable(self,
                      inter: Interaction):
        """
        Disable automatic claiming of POAPs.
        """
        res = await self.db.poap_users.delete_one({"user_id": inter.user.id})
        if res.deleted_count == 0:
            e = Embed()
            e.title = "Could not disable automatic claiming of POAPs"
            e.description = "You do not currently have automatic claiming of POAPs enabled.\n" \
                            f"Use the {await self.mention_command('enable')} command to enable it."
        else:
            e = Embed()
            e.title = "Successfully Disabled POAP Automatic Claim"
            e.description = "Your POAPs will no longer automatically be claimed for you!"

        await inter.response.send_message(embed=e, ephemeral=True)

    async def process_next_user(self):
        # get the user with the oldest last_update
        user = await self.db.poap_users.find_one_and_update(
            {},
            {"$set": {"last_updated": datetime.now(timezone.utc)}},
            sort=[("last_updated", 1)])

        if not user:
            return

        # get all their deliveries that are above the last_claimed_id
        deliveries = await self.db.poap_deliveries.find(
            {"address": user["address"].lower(), "delivery_id": {"$gt": user["last_delivery_id"]}}
        ).to_list(None)

        if not deliveries:
            return

        # loop through each delivery and try to claim it
        for delivery in deliveries:
            res = await self.claim_delivery(delivery["address"], delivery["delivery_id"])
            if not res:
                break
            # update the last_delivery_id. append delivery_id to claimed if res is 2
            await self.db.poap_users.update_one(
                {"user_id": user["user_id"]},
                {"$set" : {"last_delivery_id": delivery["delivery_id"]},
                 "$push": {"claimed": delivery["delivery_id"]}} if res == 2 else {}
            )
            await asyncio.sleep(5)

    async def claim_delivery(self, address, delivery_id):
        # get the delivery
        async with aiohttp.ClientSession(
                headers=self.session_headers) as session:
            body = {
                "id"     : delivery_id,
                "address": address,
            }
            # encode the body as json
            body = json.dumps(body)
            async with session.post("https://api.poap.tech/actions/claim-delivery-v2",
                                    data=body) as resp:
                data = await resp.json()
                if "queue_id" in data:
                    return 2
                if data.get("message") == "Delivery already claimed":
                    return 1
                if resp.status != 200:
                    log.error(f"Error claiming delivery {delivery_id} for {address}: {data}")
                    return False


async def setup(self):
    await self.add_cog(Poap(self))
