import utils.solidity as units
from utils.cfg import cfg


def uptime(time):
  parts = []

  days, time = time // units.days, time % units.days
  if days:
    parts.append('%d day%s' % (days, 's' if days != 1 else ''))

  hours, time = time // units.hours, time % units.hours
  if hours:
    parts.append('%d hour%s' % (hours, 's' if hours != 1 else ''))

  minutes, time = time // units.minutes, time % units.minutes
  if minutes:
    parts.append('%d minute%s' % (minutes, 's' if minutes != 1 else ''))

  if time or not parts:
    parts.append('%.2f seconds' % time)

  return " ".join(parts[:2])


def hex(string):
  return f"{string[:10]}..."


def etherscan_url(target, name=None):
  if not name:
    name = hex(target)
  chain = cfg["rocketpool.chain"]
  prefix = chain + "." if chain != "mainnet" else ""
  return f"[{name}](https://{prefix}etherscan.io/search?q={target})"
