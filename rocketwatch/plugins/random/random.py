import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta

import aiohttp
import pytz
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, ens, el_explorer_url
from utils.readable import uptime
from utils.rocketpool import rp
from utils.sea_creatures import sea_creatures, get_sea_creature_for_address, get_holding_for_address
from utils.shared_w3 import w3
from utils.visibility import is_hidden, is_hidden_weak

log = logging.getLogger("random")
log.setLevel(cfg["log_level"])


class Random(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def dev_time(self, ctx: Context):
        """Timezones too confusing to you? Well worry no more, this command is here to help!"""
        e = Embed()
        time_format = "%A %H:%M:%S %Z"

        dev_time = datetime.now(tz=pytz.timezone("UTC"))
        # seconds since midnight
        midnight = dev_time.replace(hour=0, minute=0, second=0, microsecond=0)
        percentage_of_day = (dev_time - midnight).seconds / (24 * 60 * 60)
        # convert to uint16
        uint_day = int(percentage_of_day * 65535)
        # generate binary string
        binary_day = f"{uint_day:016b}"
        e.add_field(name="Coordinated Universal Time",
                    value=f"{dev_time.strftime(time_format)}\n"
                          f"`{binary_day} (0x{uint_day:04x})`",
                    inline=False)

        dev_time = datetime.now(tz=pytz.timezone("Australia/Lindeman"))
        e.add_field(name="Time for most of the Dev Team", value=dev_time.strftime(time_format), inline=False)

        joe_time = datetime.now(tz=pytz.timezone("America/New_York"))
        e.add_field(name="Joe's Time", value=joe_time.strftime(time_format), inline=False)

        nick_time = datetime.now(tz=pytz.timezone("Pacific/Auckland"))
        e.add_field(name="Maverick's Time", value=nick_time.strftime(time_format), inline=False)

        await ctx.send(embed=e)

    @hybrid_command()
    async def sea_creatures(self, ctx: Context, address: str = None):
        """List all sea creatures with their required minimum holding."""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        if address is not None:
            try:
                if ".eth" in address:
                    address = ens.resolve_name(address)
                address = w3.toChecksumAddress(address)
            except (ValueError, TypeError):
                e.description = "Invalid address"
                await ctx.send(embed=e)
                return
            creature = get_sea_creature_for_address(address)
            if not creature:
                e.description = f"No sea creature for {address}"
            else:
                # get the required holding from the dictionary
                required_holding = [h for h, c in sea_creatures.items() if c == creature[0]][0]
                e.add_field(name="Visualization", value=el_explorer_url(address, prefix=creature), inline=False)
                e.add_field(name="Required holding for emoji", value=f"{required_holding * len(creature)} ETH", inline=False)
                holding = get_holding_for_address(address)
                e.add_field(name="Actual Holding", value=f"{holding:.0f} ETH", inline=False)
        else:
            e.title = "Possible Sea Creatures"
            e.description = "RPL (both old and new), rETH and ETH are consider as assets for the sea creature determination!"
            for holding_value, sea_creature in sea_creatures.items():
                e.add_field(name=f"{sea_creature}:", value=f"holds over {holding_value} ETH worth of assets",
                            inline=False)
        await ctx.send(embed=e)
        return

    async def _smoothie(self, ctx: Context):
        """Show smoothing pool information."""
        try:
            rp.get_address_by_name("rocketSmoothingPool")
        except Exception as err:
            log.exception(err)
            await ctx.send("redstone not deployed yet", ephemeral=True)
            return
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        e = Embed(title="Smoothing Pool")
        smoothie_eth = solidity.to_float(w3.eth.get_balance(rp.get_address_by_name("rocketSmoothingPool")))
        # nodes
        nodes = rp.call("rocketNodeManager.getNodeAddresses", 0, 10_000)
        node_manager = rp.get_contract_by_name("rocketNodeManager")
        node_is_smoothie = rp.multicall.aggregate(
            node_manager.functions.getSmoothingPoolRegistrationState(a) for a in nodes)
        node_is_smoothie = [r.results[0] for r in node_is_smoothie.results]
        minipool_manager = rp.get_contract_by_name("rocketMinipoolManager")
        node_minipool_count = rp.multicall.aggregate(
            minipool_manager.functions.getNodeMinipoolCount(a) for a in nodes
        )
        node_minipool_count = [r.results[0] for r in node_minipool_count.results]
        # node counts
        total_node_count = len(nodes)
        smoothie_node_count = sum(node_is_smoothie)
        # minipool counts
        total_minipool_count = sum(node_minipool_count)
        smoothie_minipool_count = sum(mc for smoothie, mc in zip(node_is_smoothie, node_minipool_count) if smoothie)
        e.description = f"`{smoothie_node_count}/{total_node_count}` Nodes (`{smoothie_node_count / total_node_count:.0%}`)" \
                        f" have joined the Smoothing Pool.\n" \
                        f" That is `{smoothie_minipool_count}/{total_minipool_count}` Minipools " \
                        f"(`{smoothie_minipool_count / total_minipool_count:.0%}`).\n" \
                        f"The current (not overall) Balance is `{smoothie_eth:,.2f}` ETH\n\n" \
                        f"{min(smoothie_node_count, 5)} largest Nodes:\n"
        e.description += "\n".join(f"- `{mc:>4}` Minipools - Node {el_explorer_url(n)}" for mc, n in sorted(
            [[mc, n] for mc, n, s in zip(node_minipool_count, nodes, node_is_smoothie) if s],
            key=lambda x: x[0],
            reverse=True)[:min(smoothie_node_count, 5)])
        await ctx.send(embed=e)

    @hybrid_command()
    async def smoothie(self, ctx: Context):
        await self._smoothie(ctx)

    @hybrid_command()
    async def smoothing_pool(self, ctx: Context):
        await self._smoothie(ctx)

    @hybrid_command()
    async def merge_ttd(self, ctx: Context):
        """Show current merge TTD."""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embeds = [Embed()]

        embeds[0].set_author(name="🔗 Data from bordel.wtf", url="https://bordel.wtf")
        text = ""
        embeds[0].url = "https://bordel.wtf/"
        embeds[0].title = "Mainnet Merge (yes, for real this time)"
        # detect if the channel is random
        is_trading = "trading" in ctx.channel.name
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://bordel.wtf/") as resp:
                    text = await resp.text()
                    f = "%a %b %d %H:%M %Y"
                    target_time = text.split("00 at ")[1].split(" UTC")[0]
                    target_time = int(datetime.strptime(target_time, f).timestamp())
                    estimate_time = text.split("expected around ")[1].split(" UTC")[0]
                    estimate_time = int(datetime.strptime(estimate_time, f).timestamp())
                    between = []
                    if "between" in text:
                        between.append(text.split("between ")[1].split(" UTC")[0])
                        between[0] = int(datetime.strptime(between[0], f).timestamp())
                        between.append(text.split("and ")[1].split(" UTC")[0])
                        between[1] = int(datetime.strptime(between[1], f).timestamp())

                    current_hashrate = text.split("Current daily hashrate: ")[1].split("</p>")[0]
                    target_hashrate = text.split("UTC, around ")[1].split(" in the network")[0]

                    # get the latest td using w3
                    td = w3.eth.get_block("latest").totalDifficulty / 1e16
                    embeds[0].add_field(name="Current Difficulty", value=f"`{td:,.0f} Peta`")

                    ttd = text.split("Difficulty of ")[1].split(" is expected")[0]
                    ttd = int(ttd) / 1e16
                    embeds[0].add_field(name="Target Difficulty", value=f"`{ttd:,.0f} Peta`")

                    embeds[0].add_field(name="Current daily hashrate", value=f"`{current_hashrate}`")

                    embeds[0].description = f"For the merge to happen with the configured TTD of `{ttd:,.0f} Peta` " \
                                            f"on <t:{target_time}>, a hashrate of `{target_hashrate}` " \
                                            f"is required.\n" \
                                            f"Currently, the merge is estimated to happen around **<t:{estimate_time}>**," \
                                            f" or **<t:{estimate_time}:R>** "
                    if between:
                        embeds[0].description += f"(between <t:{between[0]}> and <t:{between[1]}>)."
                    else:
                        embeds[0].description += "."

            if not is_trading:
                for image in ["chart.png", "hashrate.png", "ttd_hash.png"]:
                    embeds.append(Embed(url="https://bordel.wtf/"))
                    embeds[-1].set_image(url=f"https://bordel.wtf/{image}?cache_burst={int(datetime.now().timestamp())}")
                embeds.append(Embed(url="https://bordel.wtf/"))
                embeds[-1].set_image(url="https://i.redd.it/wghxf2s5eki61.jpg")

        except Exception as er:
            log.error(er)
            if "Updating data" in text:
                embeds[0].description = "bordel.wtf is updating its data..."
            else:
                embeds[0].description = "something broke? ping invis about this response"
        await ctx.send(embeds=embeds)
        return

    @hybrid_command(aliases=["brodel-wtf"])
    async def create_poap_smoothie_dump(self, ctx: Context):
        """Doesn't show current merge TTD."""
        await ctx.defer(ephemeral=is_hidden(ctx))
        r = rp.get_contract_by_name("rocketNodeManager")
        f = io.StringIO()
        writer = csv.writer(f)
        first = None
        last = None
        nodes = rp.call("rocketNodeManager.getNodeAddresses", 0, 10_000)

        storage = rp.get_contract_by_name("rocketStorage")
        tmp = rp.multicall.aggregate(
            storage.functions.getNodeWithdrawalAddress(a) for a in nodes)
        tmp = [r.results[0] for r in tmp.results]
        withdrawal_addresses = dict(zip(nodes, tmp))

        finished_24h = False
        addresses = []

        # write the header
        writer.writerow(["nodeAddress", "withdrawalAddress", "timestamp", "transactionHash"])

        for i in range(15430840, w3.eth.get_block("latest").number, 1000):
            events = r.events["NodeSmoothingPoolStateChanged"].createFilter(fromBlock=i,
                                                                            toBlock=i + 1000,
                                                                            argument_filters={"state": True})
            log.debug(f"{i}-{i + 1000}")
            for event in events.get_all_entries():
                b = w3.eth.get_block(event.blockNumber)
                t = datetime.utcfromtimestamp(b.timestamp)
                n = event.args.node
                if not first:
                    first = t
                last = t
                if last - first > timedelta(hours=24):
                    finished_24h = True
                    break
                if n not in addresses:
                    writer.writerow([n, withdrawal_addresses[n], t.isoformat(), event.transactionHash.hex()])
                    addresses.append(n)
                else:
                    log.warning(f"double node {n}")
                await asyncio.sleep(0.01)
            if finished_24h:
                break

        f.seek(0)
        if finished_24h:
            txt = "successfully gathered the first 24h of events"
        else:
            txt = f"warning, only gathered {uptime((last - first).total_seconds())}"
        await ctx.send(txt, file=File(fp=f, filename="poap_smoothie.csv"))


async def setup(self):
    await self.add_cog(Random(self))
