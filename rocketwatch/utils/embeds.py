import math

import humanize
from discord import Embed, Color

from strings import _
from utils import readable
from utils.cached_ens import CachedEns
from utils.cfg import cfg
from utils.readable import etherscan_url
from utils.rocketpool import rp
from utils.shared_w3 import w3


class CustomEmbeds:
  ens = CachedEns()

  def prepare_args(self, args):
    # handle numbers and hex strings
    for arg_key, arg_value in list(args.items()):

      if any(keyword in arg_key.lower() for keyword in ["amount", "value"]) and isinstance(arg_value, int):
        if int(math.log10(arg_value)) <= 6:
          # prob not a 18 digit number
          continue
        args[arg_key] = arg_value / 10 ** 18

      if "perc" in arg_key.lower():
        args[arg_key] = arg_value / 10 ** 16

      if str(arg_value).startswith("0x"):
        name = ""
        if w3.isAddress(arg_value):
          name = rp.call("rocketDAONodeTrusted.getMemberID", arg_value)
          if not name:
            # not an odao member, try to get their ens
            name = self.ens.get_name(arg_value)
        if not name:
          # fallback when no ens name/odao id is found or when the hex isn't an address to begin with
          name = readable.hex(arg_value)

        args[f"{arg_key}_raw"] = arg_value
        if arg_key == "pubkey":
          args[arg_key] = f"[{name}](https://beaconcha.in/validator/{arg_value})"
        else:
          args[arg_key] = etherscan_url(arg_value, name)

    return args

  def assemble(self, args):
    embed = Embed(color=Color.from_rgb(235, 142, 85))
    footer_parts = ["Developed by InvisibleSymbol#2788",
                    "/donate"]
    if cfg["rocketpool.chain"] != "mainnet":
      footer_parts.insert(-1, f"Chain: {cfg['rocketpool.chain'].capitalize()}")
    embed.set_footer(text=" · ".join(footer_parts))
    embed.title = _(f"embeds.{args.event_name}.title")

    # make numbers look nice
    for arg_key, arg_value in list(args.items()):
      if any(keyword in arg_key.lower() for keyword in ["amount", "value", "total_supply", "perc"]):
        if not isinstance(arg_key, (int, float)):
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
      embed.add_field(name="Sender Address",
                      value=senders[0])

    # show block number
    if "blockNumber" in args:
      embed.add_field(name="Block Number",
                      value=f"[{args.blockNumber}](https://etherscan.io/block/{args.blockNumber})")

    # show timestamp
    times = [value for key, value in args.items() if "time" in key.lower()]
    if times:
      embed.add_field(name="Timestamp",
                      value=f"<t:{times[0]}:R> (<t:{times[0]}:f>)",
                      inline=False)
    return embed
