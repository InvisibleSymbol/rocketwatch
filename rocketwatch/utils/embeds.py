import contextlib
import datetime
import logging
import math

import discord
import humanize
import requests
from retry import retry
from cachetools.func import ttl_cache
from discord import Color
from ens import InvalidName
from etherscan_labels import Addresses

from strings import _
from utils.cached_ens import CachedEns
from utils.cfg import cfg
from utils.readable import cl_explorer_url, advanced_tnx_url, s_hex
from utils.rocketpool import rp, NoAddressFound
from utils.sea_creatures import get_sea_creature_for_address
from utils.shared_w3 import w3

ens = CachedEns()

log = logging.getLogger("embeds")
log.setLevel(cfg["log_level"])


class Embed(discord.Embed):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.colour = Color.from_rgb(235, 142, 85)
        self.set_footer_parts([])

    def set_footer_parts(self, parts):
        footer_parts = ["Developed by 0xinvis.eth",
                        "/donate"]
        if cfg["rocketpool.chain"] != "mainnet":
            footer_parts.insert(-1, f"Chain: {cfg['rocketpool.chain'].capitalize()}")
        footer_parts.extend(parts)
        self.set_footer(text=" · ".join(footer_parts))


# Convert a user-provided string into a display name and address.
# If an ens name is provided, it will be used as the display name.
# If an address is provided, the display name will either be the reverse record or the address.
# If the user input isn't sanitary, send an error message back to the user and return None, None.
async def resolve_ens(ctx, node_address):
    # if it looks like an ens, attempt to resolve it
    address = None
    if "." in node_address:
        try:
            address = ens.resolve_name(node_address)
            if not address:
                await ctx.send("ENS name not found")
                return None, None

            return node_address, address
        except InvalidName:
            await ctx.send("Invalid ENS name")
            return None, None

    # if it's just an address, look for a reverse record
    try:
        address = w3.toChecksumAddress(node_address)
    except Exception:
        await ctx.send("Invalid address")
        return None, None

    try:
        display_name = ens.get_name(node_address) or address
        return display_name, address
    except InvalidName:
        await ctx.send("Invalid address")
        return None, None


@ttl_cache(ttl=900)
def get_pdao_delegates() -> dict[str, str]:
    @retry(tries=3, delay=1)
    def _get_delegates() -> dict[str, str]:
        response = requests.get("https://delegates.rocketpool.net/api/delegates")
        return {delegate["nodeAddress"]: delegate["name"] for delegate in response.json()}

    try:
        return _get_delegates()
    except Exception:
        log.warning("Failed to fetch pDAO delegates.")
        return {}


def el_explorer_url(target, name="", prefix="", make_code=False, block="latest"):
    url = f"https://{cfg['rocketpool.execution_layer.explorer']}/search?q={target}"
    if w3.isAddress(target):
        # sanitize address
        target = w3.toChecksumAddress(target)

        # rocketscan url stuff
        rocketscan_chains = {
            "mainnet": "https://rocketscan.io",
            "holesky": "https://holesky.rocketscan.io",
        }

        if cfg["rocketpool.chain"] in rocketscan_chains:
            rocketscan_url = rocketscan_chains[cfg["rocketpool.chain"]]

            if rp.call("rocketMinipoolManager.getMinipoolExists", target, block=block):
                url = f"{rocketscan_url}/minipool/{target}"
            if rp.call("rocketNodeManager.getNodeExists", target, block=block):
                if rp.call("rocketNodeManager.getSmoothingPoolRegistrationState", target, block=block) and prefix != -1:
                    prefix += ":cup_with_straw:"
                url = f"{rocketscan_url}/node/{target}"

        n_key = f"addresses.{target}"
        if not name and (n := _(n_key)) != n_key:
            name = n

        if not name and (member_id := rp.call("rocketDAONodeTrusted.getMemberID", target, block=block)):
            if prefix != -1:
                prefix += "🔮"
            name = member_id

        if not name and (member_id := rp.call("rocketDAOSecurity.getMemberID", target, block=block)):
            if prefix != -1:
                prefix += "🔒"
            name = member_id

        if not name and (delegate_name := get_pdao_delegates().get(target)):
            if prefix != -1:
                prefix += "🏛️"
            name = delegate_name

        if not name and cfg["rocketpool.chain"] != "mainnet":
            name = s_hex(target)

        if not name:
            a = Addresses.get(target)
            # don't apply name if it has  label is one with the id "take-action", as these don't show up on the explorer
            if (not a.labels or len(a.labels) != 1 or a.labels[0].id != "take-action") and a.name and "alert" not in a.name.lower():
                name = a.name
        if not name:
            # not an odao member, try to get their ens
            name = ens.get_name(target)

        if code := w3.eth.get_code(target):
            if prefix != -1:
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
                    n = c.functions.name().call()
                    # make sure nobody is trying to inject a custom link, as there was a guy that made the name of his contract
                    # 'RocketSwapRouter](https://etherscan.io/search?q=0x16d5a408e807db8ef7c578279beeee6b228f1c1c)[',
                    # in an attempt to get people to click on his contract

                    # first, if the name has a link in it, we ignore it
                    if any(keyword in n.lower() for keyword in
                           ["http", "discord", "airdrop", "telegram", "twitter", "youtube"]):
                        log.warning(f"Contract {target} has a suspicious name: {n}")
                    else:
                        name = f"{discord.utils.remove_markdown(n, ignore_links=False)}*"

    if not name:
        # fall back to shortened address
        name = s_hex(target)
    if make_code:
        name = f"`{name}`"
    if prefix == -1:
        prefix = ""
    return f"{prefix}[{name}]({url})"


def prepare_args(args):
    for arg_key, arg_value in list(args.items()):
        # store raw value
        args[f"{arg_key}_raw"] = arg_value

        # handle numbers
        if any(keyword in arg_key.lower() for keyword in ["amount", "value", "rate", "totaleth", "stakingeth", "rethsupply", "rplprice"]) and isinstance(arg_value, int):
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
            prefix = ""

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
    if args.event_name in ["service_interrupted", "finality_delay_event"]:
        e.colour = Color.from_rgb(235, 86, 86)
    if "sell_rpl" in args.event_name:
        e.colour = Color.from_rgb(235, 86, 86)
    if "buy_rpl" in args.event_name or "finality_delay_recover_event" in args.event_name:
        e.colour = Color.from_rgb(86, 235, 86)
    if "price_update_event" in args.event_name:
        e.colour = Color.from_rgb(86, 235, 235)

    # do this here before the amounts are converted to a string
    if "pool_deposit" in args.event_name and args.get("amount" if "ethAmount" not in args else "ethAmount", 0) >= 1000:
        e.set_image(url="https://media.giphy.com/media/VIX2atZr8dCKk5jF6L/giphy.gif")

    if "_slash_" in args.event_name or "finality_delay_event" in args.event_name:
        e.set_image(url="https://c.tenor.com/p3hWK5YRo6IAAAAC/this-is-fine-dog.gif")

    if "_proposal_smoothie_" in args.event_name:
        e.set_image(url="https://cdn.discordapp.com/attachments/812745786638336021/1106983677130461214/butta-commie-filter.png")

    if "sdao_member_kick_multi" in args.event_name:
        e.set_image(url="https://media1.tenor.com/m/Xuv3IEoH1a4AAAAC/youre-fired-donald-trump.gif")

    match args.event_name:
        case "redstone_upgrade_triggered":
            e.set_image(url="https://cdn.dribbble.com/users/187497/screenshots/2284528/media/123903807d334c15aa105b44f2bd9252.gif")
        case "atlas_upgrade_triggered":
            e.set_image(url="https://cdn.discordapp.com/attachments/912434217118498876/1097528472567558227/DALLE_2023-04-17_16.25.46_-_an_expresive_oil_painting_of_the_atlas_2_rocket_taking_off_moon_colorfull.png")
        case "houston_upgrade_triggered":
            e.set_image(url="https://i.imgur.com/XT5qPWf.png")

    amount = args.get("amount") or args.get("ethAmount", 0)
    # make numbers look nice
    for arg_key, arg_value in list(args.items()):
        if any(keyword in arg_key.lower() for keyword in
               ["amount", "value", "total_supply", "perc", "tnx_fee", "rate", "votingpower"]):
            if not isinstance(arg_value, (int, float)) or "raw" in arg_key:
                continue
            if arg_value:
                decimal = 5 - math.floor(math.log10(abs(arg_value)))
                decimal = max(0, min(5, decimal))
                arg_value = round(arg_value, decimal)
            if arg_value == int(arg_value):
                arg_value = int(arg_value)
            args[arg_key] = humanize.intcomma(arg_value)

    has_small = _(f"embeds.{args.event_name}.description_small") != f"embeds.{args.event_name}.description_small"
    has_large = _(f"embeds.{args.event_name}.description") != f"embeds.{args.event_name}.description"

    match args.event_name:
        case "eth_deposit_event":
            threshold = 32
        case _:
            threshold = 100

    if has_small and (not has_large or amount < threshold):
        e.description = _(f"embeds.{args.event_name}.description_small", **args)
        if cfg["rocketpool.chain"] != "mainnet":
            e.description += f" ({cfg['rocketpool.chain'].capitalize()})"
        e.set_footer(text="")
        return e

    e.title = _(f"embeds.{args.event_name}.title")
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

    if "epoch" in args:
        e.add_field(name="Epoch",
                    value=f"[{args.epoch}](https://{cfg['rocketpool']['consensus_layer']['explorer']}/epoch/{args.epoch})")

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

    if "invoiceID" in args:
        e.add_field(
            name="Invoice ID",
            value=f"`{args.invoiceID}`",
            inline=False
        )

    if "contractName" in args:
        e.add_field(
            name="Contract",
            value=f"`{args.contractName}`",
            inline=False
        )

    if "settingContractName" in args:
        e.add_field(name="Contract",
                    value=f"`{args.settingContractName}`",
                    inline=False)

    if "periodLength" in args:
        e.add_field(
            name="Payment Interval",
            value=humanize.naturaldelta(datetime.timedelta(seconds=args.periodLength)),
            inline=False
        )

    if "index" in args:
        e.add_field(
            name="Index",
            value=args.index,
            inline=True
        )

    if "challengePeriod" in args:
        e.add_field(
            name="Challenge Period",
            value=humanize.naturaldelta(datetime.timedelta(seconds=args.challengePeriod)),
            inline=True
        )

    if "proposalBond" in args:
        e.add_field(
            name="Proposal Bond",
            value=f"{args.proposalBond} RPL",
            inline=True
        )

    if "challengeBond" in args:
        e.add_field(
            name="Challenge Bond",
            value=f"{args.challengeBond} RPL",
            inline=True
        )

    if "startTime" in args:
        e.add_field(
            name="Start Time",
            value=f"<t:{args.startTime}>",
            inline=False
        )

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
        n = f"0x{s_hex(args.submission.merkleRoot.hex())}"
        e.add_field(name="Merkle Tree",
                    value=f"[{n}](https://gateway.ipfs.io/ipfs/{args.submission.merkleTreeCID})")

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
    el_explorer = cfg["rocketpool.execution_layer.explorer"]
    if "blockNumber" in args:
        e.add_field(name="Block Number",
                    value=f"[{args.blockNumber}](https://{el_explorer}/block/{args.blockNumber})")

    cl_explorer = cfg["rocketpool.consensus_layer.explorer"]
    if "slot" in args:
        e.add_field(name="Slot",
                    value=f"[{args.slot}](https://{cl_explorer}/slot/{args.slot})")

    if "smoothie_amount" in args:
        e.add_field(name="Smoothing Pool Balance",
                    value=f"||{args.smoothie_amount}|| ETH")

    if "reason" in args and args["reason"]:
        e.add_field(name="Likely Revert Reason",
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

    return e
