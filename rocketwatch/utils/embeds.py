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
from utils.readable import beaconchain_url
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.sea_creatures import get_sea_creature_for_holdings
from utils.shared_w3 import w3


def etherscan_url(target, name=None, prefix=None):
    if w3.isAddress(target):
        if target in cfg["override_addresses"]:
            name = cfg["override_addresses"][target]
        if not name:
            name = rp.call("rocketDAONodeTrusted.getMemberID", target)
        if not name:
            # not an odao member, try to get their ens
            name = ens.get_name(target)
    if not name:
        # fall back to shortened address
        name = readable.hex(target)
    if prefix:
        name = prefix + name
    chain = cfg["rocketpool.chain"]
    url_prefix = chain + "." if chain != "mainnet" else ""
    return f"[{name}](https://{url_prefix}etherscan.io/search?q={target})"


def exception_fallback():
    def wrapper(func):
        @functools.wraps(func)
        async def wrapped(*args):
            try:
                return await func(*args)
            except Exception as err:
                await report_error(err, *args)
                event_name = args[2]["event"] if len(args) >= 3 and "event" in args[2] else "unknown"
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
    rpl_price = solidity.to_float(rp.call("rocketNetworkPrices.getRPLPrice"))
    reth_price = solidity.to_float(rp.call("rocketTokenRETH.getExchangeRate"))
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
            prefix = None

            if w3.isAddress(arg_value):
                # get rocketpool related holdings value for this address
                address = w3.toChecksumAddress(arg_value)
                # get their eth balance
                eth_balance = solidity.to_float(w3.eth.getBalance(address))
                # get ERC-20 token balance for this address
                tokens = w3.provider.make_request("alchemy_getTokenBalances",
                                                  [address,
                                                   [
                                                       rp.get_address_by_name("rocketTokenRPL"),
                                                       rp.get_address_by_name("rocketTokenRPLFixedSupply"),
                                                       rp.get_address_by_name("rocketTokenRETH")],
                                                   ])["result"]["tokenBalances"]
                # add their tokens to their eth balance
                for token in tokens:
                    contract_name = rp.get_name_by_address(token["contractAddress"])
                    if token["error"]:
                        continue
                    if "RPL" in contract_name:
                        eth_balance += solidity.to_float(w3.toInt(hexstr=token["tokenBalance"])) * rpl_price
                    if "RETH" in contract_name:
                        eth_balance += solidity.to_float(w3.toInt(hexstr=token["tokenBalance"])) * reth_price
                # get minipool count
                minipools = solidity.to_int(rp.call("rocketMinipoolManager.getNodeValidatingMinipoolCount", address))
                eth_balance += minipools * 16
                # add their staked RPL
                staked_rpl = solidity.to_int(rp.call("rocketNodeStaking.getNodeRPLStake", address))
                eth_balance += staked_rpl * rpl_price
                prefix = get_sea_creature_for_holdings(eth_balance)

            # handle validators
            if arg_key == "pubkey":
                args[arg_key] = beaconchain_url(arg_value)
            else:
                args[arg_key] = etherscan_url(arg_value, prefix=prefix)
    return args


def assemble(args):
    color = Color.from_rgb(235, 142, 85)
    if args.event_name == "service_interrupted":
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
        if any(keyword in arg_key.lower() for keyword in ["amount", "value", "total_supply", "perc", "tnx_fee"]):
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

    if "commission" in args:
        embed.add_field(name="Commission Rate",
                        value=f"{args.commission:.2%}",
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
    time = times[0] if times else int(datetime.datetime.now().timestamp())
    embed.add_field(name="Timestamp",
                    value=f"<t:{time}:R> (<t:{time}:f>)",
                    inline=False)

    # show the transaction fees
    if "tnx_fee" in args:
        embed.add_field(name="Transaction Fee",
                        value=f"{args.tnx_fee} ETH ({args.tnx_fee_dai} DAI)",
                        inline=False)

    return embed
