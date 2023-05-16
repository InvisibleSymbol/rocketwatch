import asyncio
import csv
import io
import logging
from datetime import datetime, timezone, timedelta

import aiohttp
import dice
import humanize
import pytz
import requests
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed, ens, el_explorer_url
from utils.readable import s_hex, uptime
from utils.rocketpool import rp
from utils.sea_creatures import sea_creatures, get_sea_creature_for_address, get_holding_for_address
from utils.shared_w3 import w3, bacon
from utils.visibility import is_hidden, is_hidden_weak

log = logging.getLogger("random")
log.setLevel(cfg["log_level"])


class Random(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @hybrid_command()
    async def dice(self, ctx: Context, dice_string: str = "1d6"):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        try:
            result = dice.roll(dice_string)
        except dice.exceptions.DiceException as e:
            await ctx.send(f"Dice Error:\n```{e}```")
            return
        except dice.exceptions.DiceFatalException as e:
            await ctx.send(f"Dice Fatal Error:\n```{e}```")
            return
        except dice.exceptions.ParseException as e:
            await ctx.send(f"Dice Parse Error:\n```{e}```")
            return
        except dice.exceptions.ParseFatalException as e:
            await ctx.send(f"Dice Parse Fatal Error:\n```{e}```")
            return
        e = Embed()
        e.title = f"🎲 {dice_string}"
        if len(str(result)) <= 2000:
            e.description = "Result too long to display, attaching as file."
            file = File(io.StringIO(str(result)), filename="dice_result.txt")
            await ctx.send(embed=e, file=file)
        else:
            e.description = f"Result: `{result}`"
            await ctx.send(embed=e)

    @hybrid_command()
    async def burn_reason(self, ctx: Context):
        """Show the current burn reason"""
        await self._burn_reason(ctx)

    @hybrid_command()
    async def br(self, ctx: Context):
        """Show the current burn reason"""
        await self._burn_reason(ctx)

    async def _burn_reason(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        url = "https://ultrasound.money/api/fees/grouped-analysis-1"
        # get data from url using aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        e = Embed()
        e.set_author(name="🔗 Data from ultrasound.money", url="https://ultrasound.money")
        description = "**Eth Burned:**\n```"
        feesburned = data["feesBurned"]
        for span in ["5m", "1h", "24h"]:
            k = f"feesBurned{span}"
            description += f"Last {span}: {solidity.to_float(feesburned[k]):,.2f} ETH ({feesburned[f'{k}Usd']:,.2f} USD)\n"
        description += "```\n"
        description += "**Burn Ranking (last 5 minutes):**\n"
        ranking = data["leaderboards"]["leaderboard5m"][:5]
        for i, entry in enumerate(ranking):
            # use a number emoji as rank (:one:, :two:, ...)
            # first of convert the number to a word
            description += f":{humanize.apnumber(i + 1)}:"
            if "address" not in entry:
                description += f" {entry['name']}"
            else:
                url = cfg["rocketpool.execution_layer.explorer"]
                if not entry["name"]:
                    entry["name"] = s_hex(entry["address"])
                target = f"[{entry['name']}](https://{url}/search?q={entry['address']})"
                description += f" {target}"
            if entry.get("category"):
                description += f" `[{entry['category'].upper()}]`"
            description += f"\n<:VOID:721787344138797116>`{solidity.to_float(entry['fees']):,.2f} ETH` :fire:\n"
        e.add_field(
            name="Current Base Fee:",
            value=f"`{solidity.to_float(data['latestBlockFees'][0]['baseFeePerGas'], 9):,.2f} GWEI`"
        )
        e.description = description
        await ctx.send(embed=e)

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
                          f"`{binary_day} (0x{uint_day:04x})`")
        b = solidity.slot_to_beacon_day_epoch_slot(int(bacon.get_block("head")["data"]["message"]["slot"]))
        e.add_field(name="Beacon Time", value=f"Day {b[0]}, {b[1]}:{b[2]}")

        dev_time = datetime.now(tz=pytz.timezone("Australia/Lindeman"))
        e.add_field(name="Time for most of the Dev Team", value=dev_time.strftime(time_format), inline=False)

        joe_time = datetime.now(tz=pytz.timezone("America/New_York"))
        e.add_field(name="Joe's Time", value=joe_time.strftime(time_format), inline=False)

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
        d = datetime.now().timestamp() - rp.call("rocketRewardsPool.getClaimIntervalTimeStart")
        e.description = f"`{smoothie_node_count}/{total_node_count}` Nodes (`{smoothie_node_count / total_node_count:.2%}`)" \
                        f" have joined the Smoothing Pool.\n" \
                        f" That is `{smoothie_minipool_count}/{total_minipool_count}` Minipools " \
                        f"(`{smoothie_minipool_count / total_minipool_count:.0%}`).\n" \
                        f"The current (not overall) Balance is `{smoothie_eth:,.2f}` ETH.\n" \
                        f"This is over a span of `{uptime(d)}`.\n\n" \
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
    async def cow(self, ctx: Context, tnx: str):
        # https://etherscan.io/tx/0x47d96c6310f08b473f2c9948d6fbeef1084f0b393c2263d2fc8d5dc624f97fe3
        if "etherscan.io/tx/" not in tnx:
            await ctx.send("nop", ephemeral=True)
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        e = Embed()
        url = tnx.replace("etherscan.io", "explorer.cow.fi")
        e.description = f"[cow explorer]({url})"
        await ctx.send(embed=e)

    @hybrid_command()
    async def odao_challenges(self, ctx: Context):
        """Shows the current oDAO challenges."""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        c = rp.get_contract_by_name("rocketDAONodeTrustedActions")
        # get challenges made
        events = c.events["ActionChallengeMade"].createFilter(
            fromBlock=w3.eth.get_block("latest").number - 7 * 24 * 60 * 60 // 12)
        # get all events
        events = events.get_all_entries()
        # remove all events of nodes that aren't challenged anymore
        for event in events:
            if not rp.call("rocketDAONodeTrusted.getMemberIsChallenged", event.args.nodeChallengedAddress):
                events.remove(event)
        # sort by block number
        events.sort(key=lambda x: x.blockNumber)
        if not events:
            await ctx.send("no active challenges found")
            return
        e = Embed(title="Active oDAO Challenges")
        e.description = ""
        # get duration of challenge period
        challenge_period = rp.call("rocketDAONodeTrustedSettingsMembers.getChallengeWindow")
        for event in events:
            time_left = challenge_period - (w3.eth.get_block("latest").timestamp - event.args.time)
            time_left = uptime(time_left, True)
            e.description += f"**{el_explorer_url(event.args.nodeChallengedAddress)}** was challenged by **{el_explorer_url(event.args.nodeChallengerAddress)}**\n"
            e.description += f"Time Left: **{time_left}**\n\n"
        await ctx.send(embed=e)


async def setup(self):
    await self.add_cog(Random(self))
