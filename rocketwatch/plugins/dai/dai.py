import logging

import aiohttp
import humanize
import requests
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, el_explorer_url
from utils.shared_w3 import w3
from utils.visibility import is_hidden_weak, is_hidden

log = logging.getLogger("dai")
log.setLevel(cfg["log_level"])


class MakerAPI:
    def __init__(self):
        self.password = cfg["makerdao.password"]
        self.email = cfg["makerdao.email"]
        self.api_url = cfg["makerdao.api"]
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")
        self.col = self.db["makerdao_api_tokens"]
        self.initialized = False

    async def _async_init(self):
        await self.col.create_index("ts", expireAfterSeconds=50 * 60)

    async def _get_bearer(self):
        if not self.initialized:
            await self._async_init()
            self.initialized = True
        d = (await self.col.find_one({"_id": "access_token"})) or (await self._refresh_db_bearer())
        return d["access_token"]

    async def _refresh_db_bearer(self):
        log.warning("requesting new bearer token")
        form_data = aiohttp.FormData()
        form_data.add_field("username", self.email)
        form_data.add_field("password", self.password)
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.api_url}/v1/login/access-token",
                                    data=form_data) as resp:
                data = await resp.json()
                assert "access_token" in data
                await self.col.update_one({"_id": "access_token"},
                                          {
                                              "$currentDate": {
                                                  "ts": True
                                              },
                                              "$set"        : data
                                          }, upsert=True)
        return data

    async def _call_api(self, endpoint, *args, **kwargs):
        url = f"{self.api_url}/{endpoint}"
        kwargs["headers"] = {"Authorization": f"Bearer {await self._get_bearer()}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, *args, **kwargs) as resp:
                data = await resp.json()
                return data

    async def get_vaults(self, ilk="RETH-A"):
        # get vaults using v1/vaults/current_state
        # limit = 100
        # offset identifier = "skip"

        result = []
        offset = 0
        while True:
            data = await self._call_api("v1/vaults/current_state", params={"ilk": ilk, "limit": 100, "skip": offset})

            if not data:
                break
            result.extend(data)
            offset += 100
        return result


class DAI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api = MakerAPI()

    @hybrid_command()
    async def dai_stats(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        cdata = requests.get("https://api.makerburn.com/history/reth_a").json()
        if "history" not in cdata or not cdata["history"]:
            await ctx.send("ayo?")
            return
        cd = cdata["history"][-1]
        sdata = requests.get("https://api.makerburn.com/status").json()
        if "collateral_list" not in sdata or not (
                sd := next(iter([s for s in sdata["collateral_list"] if s["type"] == "reth_a"])), None):
            await ctx.send("ayo?")
            return
        e = Embed()
        e.add_field(name="DAI Minted:", value=f"{int(cd['dai_total']):,} DAI")
        e.add_field(name="Debt Ceiling:", value=f"{humanize.intword(cd['temp_dai_cap'])}/{humanize.intword(cd['dai_cap'])} USD")
        e.add_field(name="rETH Locked:", value=f"{sd['locked']:,.2f} rETH", inline=False)
        stability_fee = sd["fee"] ** solidity.years - 1
        e.add_field(name="Stability Fee:", value=f"{stability_fee:.2%}")
        e.add_field(name="Liquidation Ratio:", value=f"{sd['liq_ratio']:.0%}")
        await ctx.send(embed=e)

    @hybrid_command()
    async def maker_vaults(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        vaults = await self.api.get_vaults()
        vaults = sorted(vaults, key=lambda x: x["collateral"], reverse=True)
        vaults = vaults[:5]
        e.description = "**5 Largest Vaults:**\n"
        for v in vaults:
            e.description += f"{el_explorer_url(w3.toChecksumAddress(v['owner'])) if v['owner'] else '???'}:\n" \
                             f"<:VOID:721787344138797116>Borrowed `{int(v['principal']):,} DAI`" \
                             f" with `{v['collateral']:.2f} rETH` as collateral (`{int(v['collateralization']*100)}%`)\n" \
                             f"<:VOID:721787344138797116>Liquidation Price: `{int(v['liquidation_price'])} rETH/DAI`" \
                             f" Accrued Fees: `{v['accrued_fees']:.2f} DAI`\n"
        await ctx.send(embed=e)


async def setup(self):
    await self.add_cog(DAI(self))
