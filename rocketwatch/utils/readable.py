import base64
import json

import utils.solidity as units
from utils import pako
from utils.cfg import cfg


def prettify_json_string(data):
    return json.dumps(json.loads(data), indent=4)


def decode_abi(compressed_string):
    inflated = pako.pako_inflate(base64.b64decode(compressed_string))
    return inflated.decode("ascii")


def uptime(time, highres= False):
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

    return " ".join(parts[:2] if not highres else parts)


def s_hex(string):
    return string[:10]


def cl_explorer_url(target, name=None):
    if not name and isinstance(target, str):
        name = s_hex(target)
    else:
        name = target
    url = cfg["rocketpool.consensus_layer.explorer"]
    return f"[{name}](https://{url}/validator/{target})"


def advanced_tnx_url(tx_hash):
    chain = cfg["rocketpool.chain"]
    if chain not in ["mainnet", "goerli"]:
        return ""
    return f"[[A]](https://ethtx.info/{chain}/{tx_hash})"
