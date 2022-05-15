import datetime
import math

import discord
import humanize
from discord import Color
from etherscan_labels import Addresses

from strings import _
from utils import solidity
from utils.cached_ens import CachedEns
from utils.cfg import cfg
from utils.readable import cl_explorer_url, advanced_tnx_url, s_hex
from utils.rocketpool import rp
from utils.sea_creatures import get_sea_creature_for_holdings, get_sea_creature_for_address
from utils.shared_w3 import w3

ens = CachedEns()


class Embed(discord.Embed):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.colour = Color.from_rgb(235, 142, 85)
        footer_parts = ["Developed by 0xinvis.eth",
                        "/donate for POAP"]
        if cfg["rocketpool.chain"] != "mainnet":
            footer_parts.insert(-1, f"Chain: {cfg['rocketpool.chain'].capitalize()}")
        self.set_footer(text=" · ".join(footer_parts))


def el_explorer_url(target, name="", prefix=""):
    if w3.isAddress(target):
        if target in cfg["override_addresses"]:
            name = cfg["override_addresses"][target]
        if not name:
            name = rp.call("rocketDAONodeTrusted.getMemberID", target)
        if not name:
            a = Addresses.get(target)
            # don't apply name if its only label is one with the id "take-action", as these don't show up on the explorer
            if not a.labels or len(a.labels) != 1 or a.labels[0].id != "take-action":
                name = a.name
        if not name:
            # not an odao member, try to get their ens
            name = ens.get_name(target)

        if code := w3.eth.get_code(target):
            prefix += "📄"
            if (
                not name
                and code.hex()
                == "0x3d60323d7353db332d01e0ba9c3529110899aa00c719c2438a330382573d601436036020363d600c37343d515af1905780fd5b"
            ):
                name = "MEV Bot Contract"
    if not name:
        # fall back to shortened address
        name = s_hex(target)
    if prefix:
        name = prefix + name
    url = cfg["rocketpool.execution_layer.explorer"]
    return f"[{name}](https://{url}/search?q={target})"


def prepare_args(args):
    for arg_key, arg_value in list(args.items()):
        # store raw value
        args[f"{arg_key}_raw"] = arg_value

        # handle numbers
        if any(keyword in arg_key.lower() for keyword in ["amount", "value", "rate"]) and isinstance(arg_value, int):
            args[arg_key] = arg_value / 10 ** 18

        # handle timestamps
        if "deadline" in arg_key.lower() and isinstance(arg_value, int):
            args[arg_key] = f"<t:{arg_value}:f>(<t:{arg_value}:R>)"

        # handle percentages
        if "perc" in arg_key.lower():
            args[arg_key] = arg_value / 10 ** 16

        # handle hex strings
        if str(arg_value).startswith("0x"):
            prefix = None

            if w3.isAddress(arg_value):
                # get rocketpool related holdings value for this address
                address = w3.toChecksumAddress(arg_value)
                prefix = get_sea_creature_for_address(address)

            # handle validators
            if arg_key == "pubkey":
                args[arg_key] = cl_explorer_url(arg_value)
            else:
                args[arg_key] = el_explorer_url(arg_value, prefix=prefix)
                args[f'{arg_key}_clean'] = el_explorer_url(arg_value)
                if len(arg_value) == 66:
                    args[f'{arg_key}_small'] = el_explorer_url(arg_value, name="[tnx]")
    return args


def assemble(args):
    e = Embed()
    if args.event_name == "service_interrupted":
        e.colour = Color.from_rgb(235, 86, 86)

    do_small = all([
        _(f"embeds.{args.event_name}.description_small"),
        args.get("amount" if "ethAmount" not in args else "ethAmount") > 100])

    if not do_small:
        e.title = _(f"embeds.{args.event_name}.title")

    # make numbers look nice
    for arg_key, arg_value in list(args.items()):
        if any(keyword in arg_key.lower() for keyword in ["amount", "value", "total_supply", "perc", "tnx_fee", "rate"]):
            if not isinstance(arg_value, (int, float)) or "raw" in arg_key:
                continue
            if arg_value:
                decimal = 5 - math.floor(math.log10(arg_value))
                decimal = max(0, min(5, decimal))
                arg_value = round(arg_value, decimal)
            if arg_value == int(arg_value):
                arg_value = int(arg_value)
            args[arg_key] = humanize.intcomma(arg_value)

    if do_small:
        e.description = _(f"embeds.{args.event_name}.description_small", **args)
        e.set_footer(text="")
        return e

    e.description = _(f"embeds.{args.event_name}.description", **args)

    if "exchangeRate" in args:
        e.add_field(name="Exchange Rate",
                    value=f"`{args.exchangeRate} RPL/{args.otherToken}`" +
                          (f" (`{args.discountAmount}%` Discount, oDAO: `{args.marketExchangeRate} RPL/ETH`)" if "discountAmount" in args else ""),
                    inline=False)

    # show public key if we have one
    if "pubkey" in args:
        e.add_field(name="Validator",
                    value=args.pubkey,
                    inline=False)

    if "timezone" in args:
        e.add_field(name="Timezone",
                    value=f"`{args.timezone}`",
                    inline=False)

    if "node_operator" in args:
        e.add_field(name="Node Operator",
                    value=args.node_operator)

    if "slashing_type" in args:
        e.add_field(name="Reason",
                    value=f"`{args.slashing_type} Violation`")

    if "commission" in args:
        e.add_field(name="Commission Rate",
                    value=f"{args.commission:.2%}",
                    inline=False)

    if "settingContractName" in args:
        e.add_field(name="Contract",
                    value=f"`{args.settingContractName}`",
                    inline=False)

    if "invoiceID" in args:
        e.add_field(name="Invoice ID",
                    value=f"`{args.invoiceID}`",
                    inline=False)

    if "contractAddress" in args and "Contract" in args.type:
        e.add_field(name="Contract Address",
                    value=args.contractAddress,
                    inline=False)

    if "url" in args:
        e.add_field(name="URL",
                    value=args.url,
                    inline=False)

    # show current inflation
    if "inflation" in args:
        e.add_field(name="Current Inflation",
                    value=f"{args.inflation}%",
                    inline=False)

    # show transaction hash if possible
    if "transactionHash" in args:
        content = f"{args.transactionHash}{advanced_tnx_url(args.transactionHash_raw)}"
        e.add_field(name="Transaction Hash",
                    value=content)

    # show sender address
    if senders := [value for key, value in args.items() if key.lower() in ["sender", "from"]]:
        sender = senders[0]
        e.add_field(name="Sender Address",
                    value=sender)

    # show block number
    if "blockNumber" in args:
        e.add_field(name="Block Number",
                    value=f"[{args.blockNumber}](https://etherscan.io/block/{args.blockNumber})")

    if "reason" in args and args["reason"]:
        e.add_field(name="Revert Reason",
                    value=f"`{args.reason}`",
                    inline=False)

    # show timestamp
    times = [value for key, value in args.items() if "time" in key.lower()]
    time = times[0] if times else int(datetime.datetime.now().timestamp())
    e.add_field(name="Timestamp",
                value=f"<t:{time}:R> (<t:{time}:f>)",
                inline=False)

    # show the transaction fees
    if "tnx_fee" in args:
        e.add_field(name="Transaction Fee",
                    value=f"{args.tnx_fee} ETH ({args.tnx_fee_dai} DAI)",
                    inline=False)

    if "_slash_" in args.event_name:
        e.set_image(url="https://c.tenor.com/p3hWK5YRo6IAAAAC/this-is-fine-dog.gif")

    return e
