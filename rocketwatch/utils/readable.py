import base64
import contextlib
import json

import utils.solidity as units
from utils import pako
from utils.cfg import cfg
from utils.shared_w3 import bacon


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
    # if name is none, and it has the correct length for a validator pubkey, try to lookup the validator index
    if not name and isinstance(target, str) and len(target) == 98:
        with contextlib.suppress(Exception):
            if v := bacon.get_validator(target)["data"]["index"]:
                name = f"#{v}"
    if not name and isinstance(target, str):
        name = s_hex(target)
    if not name:
        name = target
    url = cfg["rocketpool.consensus_layer.explorer"]
    return f"[{name}](https://{url}/validator/{target})"


def advanced_tnx_url(tx_hash):
    chain = cfg["rocketpool.chain"]
    if chain not in ["mainnet", "goerli"]:
        return ""
    return f"[[A]](https://ethtx.info/{chain}/{tx_hash})"


def render_tree(data: dict, name: str) -> str:
    # remove empty states
    data = {k: v for k, v in data.items() if v}
    strings = []
    values = []
    for i, (state, substates) in enumerate(data.items()):
        c = sum(substates.values())
        l = "├" if i != len(data) - 1 else "└"
        strings.append( f" {l}{state.title()}: ")
        values.append(c)
        l = "│" if i != len(data) - 1 else " "
        for j, (substate, count) in enumerate(substates.items()):
            sl = "├" if j != len(substates) - 1 else "└"
            strings.append(f" {l} {sl}{substate.title()}: ")
            values.append(count)
    # longest string offset
    max_left_len = max(len(s) for s in strings)
    max_right_len = max(len(str(v)) for v in values)
    # right align all values
    for i, v in enumerate(values):
        strings[i] = strings[i].ljust(max_left_len) + str(v).rjust(max_right_len)
    description = f"{name}:\n"
    description += "\n".join(strings)
    return description


def sanitize_for_markdown(text):
    reserved_characters = ['\\', '*', '_', '{', '}', '[', ']', '(', ')', '#', '+', '-', '.', '!', '|']
    for char in reserved_characters:
        text = text.replace(char, '\\' + char)
    # strip urls
    
    return text
