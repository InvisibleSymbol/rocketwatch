import math

import humanize
from discord import Embed, Color

from strings import _


def assemble(args):
  embed = Embed(color=Color.from_rgb(235, 142, 85))
  embed.set_footer(text="Developed by InvisibleSymbol#2788 · /donate")
  embed.title = _(f"embeds.{args.event_name}.title")

  # make numbers look nice
  for arg_key, arg_value in list(args.items()):
    if any(keyword in arg_key.lower() for keyword in ["amount", "value"]):
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
                    value=args.pubkey_fancy,
                    inline=False)

  # show current inflation
  if "inflation" in args:
    embed.add_field(name="Current Inflation",
                    value=f"{args.inflation}%",
                    inline=False)

  # show transaction hash if possible
  if "transactionHash" in args:
    embed.add_field(name="Transaction Hash",
                    value=args.transactionHash_fancy)

  # show sender address
  if "from" in args:
    embed.add_field(name="Sender Address",
                    value=args.from_fancy)

  # show block number
  if "blockNumber" in args:
    embed.add_field(name="Block Number",
                    value=f"[{args.blockNumber}](https://goerli.etherscan.io/block/{args.blockNumber})")

  # show timestamp
  times = [value for key, value in args.items() if "time" in key.lower()]
  if times:
    embed.add_field(name="Timestamp",
                    value=f"<t:{times[0]}:R> (<t:{times[0]}:f>)",
                    inline=False)
  return embed
