import contextlib
import datetime
import math

import discord
import humanize
from discord import Color
from etherscan_labels import Addresses

from strings import _
from utils.cached_ens import CachedEns
from utils.cfg import cfg
from utils.readable import cl_explorer_url, advanced_tnx_url, s_hex
from utils.rocketpool import rp
from utils.sea_creatures import get_sea_creature_for_address
from utils.shared_w3 import w3

ens = CachedEns()


class Embed(discord.Embed):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.colour = Color.from_rgb(235, 142, 85)
        footer_parts = ["Developed by 0xinvis.eth",
                        "/donate"]
        if cfg["rocketpool.chain"] != "mainnet":
            footer_parts.insert(-1, f"Chain: {cfg['rocketpool.chain'].capitalize()}")
        self.set_footer(text=" · ".join(footer_parts))


def el_explorer_url(target, name="", prefix=""):
    url = f"https://{cfg['rocketpool.execution_layer.explorer']}/search?q={target}"
    if w3.isAddress(target):
        # rocketscan url stuff
        if rp.call("rocketMinipoolManager.getMinipoolExists", target):
            if cfg["rocketpool.chain"] == "goerli":
                url = f"https://prater.rocketscan.io/minipool/{target}"
            else:
                url = f"https://rocketscan.io/minipool/{target}"
        if rp.call("rocketNodeManager.getNodeExists", target):
            if cfg["rocketpool.chain"] == "goerli":
                url = f"https://prater.rocketscan.io/node/{target}"
            else:
                url = f"https://rocketscan.io/node/{target}"

        if target in cfg["override_addresses"]:
            name = cfg["override_addresses"][target]

        if cfg["rocketpool.chain"] != "mainnet" and not name:
            name = s_hex(target)

        if not name and (member_id := rp.call("rocketDAONodeTrusted.getMemberID", target)):
            prefix += "🔮"
            name = member_id
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
                    and w3.keccak(text=code.hex()).hex()
                    in cfg["mev.hashes"]
            ):
                name = "MEV Bot Contract"
            if not name:
                with contextlib.suppress(Exception):
                    c = w3.eth.contract(address=target, abi=[{"inputs"         : [],
                                                              "name"           : "name",
                                                              "outputs"        : [{"internalType": "string",
                                                                                   "name"        : "",
                                                                                   "type"        : "string"}],
                                                              "stateMutability": "view",
                                                              "type"           : "function"}])
                    name = c.functions.name().call()
    if not name:
        # fall back to shortened address
        name = s_hex(target)
    if prefix:
        name = prefix + name
    return f"[{name}]({url})"


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
        if arg_key.lower() in ["rate", "penalty"]:
            args[f"{arg_key}_perc"] = arg_value / 10 ** 16

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
            elif arg_key == "cow_uid":
                args[arg_key] = f"[ORDER](https://explorer.cow.fi/orders/{arg_value})"
            else:
                args[arg_key] = el_explorer_url(arg_value, prefix=prefix)
                args[f'{arg_key}_clean'] = el_explorer_url(arg_value)
                if len(arg_value) == 66:
                    args[f'{arg_key}_small'] = el_explorer_url(arg_value, name="[tnx]")
    if "from" in args:
        args["fancy_from"] = args["from"]
        if "caller" in args and args["from"] != args["caller"]:
                args["fancy_from"] = f"{args['caller']} ({args['from']})"
    return args


def assemble(args):
    e = Embed()
    if args.event_name == "service_interrupted":
        e.colour = Color.from_rgb(235, 86, 86)
    if "sell" in args.event_name:
        e.colour = Color.from_rgb(235, 86, 86)
    if "buy" in args.event_name:
        e.colour = Color.from_rgb(86, 235, 86)

    do_small = all([
        _(f"embeds.{args.event_name}.description_small") != f"embeds.{args.event_name}.description_small",
        args.get("amount" if "ethAmount" not in args else "ethAmount", 0) < 100])

    if not do_small:
        e.title = _(f"embeds.{args.event_name}.title")

    # make numbers look nice
    for arg_key, arg_value in list(args.items()):
        if any(keyword in arg_key.lower() for keyword in
               ["amount", "value", "total_supply", "perc", "tnx_fee", "rate"]):
            if not isinstance(arg_value, (int, float)) or "raw" in arg_key:
                continue
            if arg_value:
                decimal = 5 - math.floor(math.log10(abs(arg_value)))
                decimal = max(0, min(5, decimal))
                arg_value = round(arg_value, decimal)
            if arg_value == int(arg_value):
                arg_value = int(arg_value)
            args[arg_key] = humanize.intcomma(arg_value)

    if do_small:
        e.description = _(f"embeds.{args.event_name}.description_small", **args)
        if cfg["rocketpool.chain"] != "mainnet":
            e.description += f" ({cfg['rocketpool.chain'].capitalize()})"
        e.set_footer(text="")
        return e

    e.description = _(f"embeds.{args.event_name}.description", **args)

    if "cow_uid" in args:
        e.add_field(name="Cow Order",
                    value=args.cow_uid,
                    inline=False)

    if "exchangeRate" in args:
        e.add_field(name="Exchange Rate",
                    value=f"`{args.exchangeRate} RPL/{args.otherToken}`" +
                          (
                              f" (`{args.discountAmount}%` Discount, oDAO: `{args.marketExchangeRate} RPL/ETH`)" if "discountAmount" in args else ""),
                    inline=False)

    """
    # show public key if we have one
    if "pubkey" in args:
        e.add_field(name="Validator",
                    value=args.pubkey,
                    inline=False)
    """

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

    """
    if "commission" in args:
        e.add_field(name="Commission Rate",
                    value=f"{args.commission:.2%}",
                    inline=False)
    """
    
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

    if "submission" in args and "merkleTreeCID" in args.submission:
        e.add_field(name="IPFS Merkle Tree",
                    value=f"[{s_hex(args.submission.merkleTreeCID)}](https://gateway.ipfs.io/ipfs/{args.submission.merkleTreeCID})")

    # show transaction hash if possible
    if "transactionHash" in args:
        content = f"{args.transactionHash}{advanced_tnx_url(args.transactionHash_raw)}"
        e.add_field(name="Transaction Hash",
                    value=content)

    # show sender address
    if senders := [value for key, value in args.items() if key.lower() in ["sender", "from"]]:
        sender = senders[0]
        v = sender
        # if args["origin"] is an address and does not match the sender, show both
        if "caller" in args and args["caller"] != sender and "0x" in args["caller"]:
            v = f"{args.caller} ({sender})"
        e.add_field(name="Sender Address",
                    value=v)

    # show block number
    if "blockNumber" in args:
        e.add_field(name="Block Number",
                    value=f"[{args.blockNumber}](https://etherscan.io/block/{args.blockNumber})")

    if "reason" in args and args["reason"]:
        e.add_field(name="Revert Reason",
                    value=f"`{args.reason}`",
                    inline=False)

    # show timestamp
    if "time" in args.keys():
        times = [args["time"]]
    else:
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

    if "_proposal_smoothie_" in args.event_name:
        e.set_image(url="https://i.kym-cdn.com/photos/images/original/001/866/880/db1.png")
    return e
