import datetime
import functools
import math

import humanize
from discord import Embed, Color
from web3.datastructures import MutableAttributeDict as aDict

from strings import _
from utils import solidity, readable
from utils.cached_ens import CachedEns
from utils.cfg import cfg
from utils.containers import Response
from utils.readable import etherscan_url, beaconchain_url
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.shared_w3 import w3


def exception_fallback():
    def wrapper(func):
        @functools.wraps(func)
        async def wrapped(*args):
            try:
                return await func(*args)
            except Exception as err:
                await report_error(err, *args)
                event_name = args[2]["event"] if len(args) >= 3 and "event" in args[2] else "unkown"
                # create fallback embed
                e = assemble(aDict({
                    "event_name"         : "fallback",
                    "fallback_event_name": event_name
                }))
                return Response(
                    embed=e,
                    event_name=event_name
                )

        return wrapped

    return wrapper


ens = CachedEns()


def prepare_args(args):
    for arg_key, arg_value in list(args.items()):
        # store raw value
        args[f"{arg_key}_raw"] = arg_value

        # handle numbers
        if any(keyword in arg_key.lower() for keyword in ["amount", "value"]) and isinstance(arg_value, int):
            args[arg_key] = arg_value / 10 ** 18

        # handle percentages
        if "perc" in arg_key.lower():
            args[arg_key] = arg_value / 10 ** 16

        # handle hex strings
        if str(arg_value).startswith("0x"):
            name = None

            # handle addresses
            if w3.isAddress(arg_value):
                if arg_value in cfg["override_addresses"]:
                    name = cfg["override_addresses"][arg_value]
                if not name:
                    name = rp.call("rocketDAONodeTrusted.getMemberID", arg_value)
                if not name:
                    # not an odao member, try to get their ens
                    name = ens.get_name(arg_value)
                if not name:
                    # fall back to shortened address
                    name = readable.hex(arg_value)
                # get balance of address and add whale emoji if above 100 ETH
                balance = solidity.to_float(w3.eth.getBalance(w3.toChecksumAddress(arg_value)))
                if balance > 100:
                    name = f"🐳 {name}"

            # handle validators
            if arg_key == "pubkey":
                args[arg_key] = beaconchain_url(arg_value)
            else:
                args[arg_key] = etherscan_url(arg_value, name)
    return args


def assemble(args):
    color = Color.from_rgb(235, 142, 85)
    if args.event_name == "fallback":
        color = Color.from_rgb(235, 86, 86)
    embed = Embed(color=color)
    footer_parts = ["Developed by InvisibleSymbol#2788",
                    "/donate for POAP"]
    if cfg["rocketpool.chain"] != "mainnet":
        footer_parts.insert(-1, f"Chain: {cfg['rocketpool.chain'].capitalize()}")
    embed.set_footer(text=" · ".join(footer_parts))
    embed.title = _(f"embeds.{args.event_name}.title")

    # make numbers look nice
    for arg_key, arg_value in list(args.items()):
        if any(keyword in arg_key.lower() for keyword in ["amount", "value", "total_supply", "perc"]):
            if not isinstance(arg_value, (int, float)) or "raw" in arg_key:
                continue
            if arg_value:
                decimal = 5 - math.floor(math.log10(arg_value))
                decimal = max(0, min(5, decimal))
                arg_value = round(arg_value, decimal)
            if arg_value == int(arg_value):
                arg_value = int(arg_value)
            args[arg_key] = humanize.intcomma(arg_value)

    embed.description = _(f"embeds.{args.event_name}.description", **args)

    # show public key if we have one
    if "pubkey" in args:
        embed.add_field(name="Validator",
                        value=args.pubkey,
                        inline=False)

    if "settingContractName" in args:
        embed.add_field(name="Contract",
                        value=f"`{args.settingContractName}`",
                        inline=False)

    if "invoiceID" in args:
        embed.add_field(name="Invoice ID",
                        value=f"`{args.invoiceID}`",
                        inline=False)

    if "contractAddress" in args and "Contract" in args.type:
        embed.add_field(name="Contract Address",
                        value=args.contractAddress,
                        inline=False)

    if "url" in args:
        embed.add_field(name="URL",
                        value=args.url,
                        inline=False)

    # show current inflation
    if "inflation" in args:
        embed.add_field(name="Current Inflation",
                        value=f"{args.inflation}%",
                        inline=False)

    # show transaction hash if possible
    if "transactionHash" in args:
        embed.add_field(name="Transaction Hash",
                        value=args.transactionHash)

    # show sender address
    senders = [value for key, value in args.items() if key.lower() in ["sender", "from"]]
    if senders:
        sender = senders[0]
        embed.add_field(name="Sender Address",
                        value=sender)

    # show block number
    if "blockNumber" in args:
        embed.add_field(name="Block Number",
                        value=f"[{args.blockNumber}](https://etherscan.io/block/{args.blockNumber})")

    # show timestamp
    times = [value for key, value in args.items() if "time" in key.lower()]
    if times:
        time = times[0]
    else:
        time = int(datetime.datetime.now().timestamp())
    embed.add_field(name="Timestamp",
                    value=f"<t:{time}:R> (<t:{time}:f>)",
                    inline=False)
    return embed
