import base64
import contextlib
import json

from colorama import Style, Fore

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


def render_tree_legacy(data: dict, name: str) -> str:
    # remove empty states
    data = {k: v for k, v in data.items() if v}
    strings = []
    values = []
    for i, (state, substates) in enumerate(data.items()):
        c = sum(substates.values())
        l = "├" if i != len(data) - 1 else "└"
        strings.append(f" {l}{state.title()}: ")
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


def render_branch(k, v, prefix, current_depth=0, max_depth=0, reverse=False, m_prev=""):
    m = "┌" if reverse else "└"
    a = [(f"{prefix}{k}:", v.get("_value", 0), current_depth)]
    # if the value is a dict, recurse
    if isinstance(v, dict) and (max_depth == 0 or current_depth < max_depth):
        # turn the prev char of the prefix from a ├ to a │
        if prefix and prefix[-2] == "├":
            prefix = f"{prefix[:-2]}│ "
        # remove the _value key as it is metadata and not part of the tree.
        v = {k: v for k, v in v.items() if not k.startswith("_")}
        for i, (sk, sv) in enumerate(v.items()):
            p = prefix
            if p and p[-2] == m_prev:
                p = p[::-1]
                p = p.replace(f"─{m_prev}", "  " if m == m_prev else " │", 1)
                p = p[::-1]
            p += "├─" if i != len(v) - 1 else f"{m}─"  # last connection
            if not reverse:
                a = list(render_branch(sk, sv, p, current_depth + 1, max_depth=max_depth, reverse=False, m_prev=m)) + a
            else:
                a.extend(render_branch(sk, sv, p, current_depth + 1, max_depth=max_depth, reverse=False, m_prev=m))
    return a


def render_tree(data: dict, name: str, max_depth: int = 0) -> str:
    # remove empty states
    data = {k: v for k, v in data.items() if v}
    lines, values, depths = map(list, zip(*list(reversed(render_branch(name, data, "", max_depth=max_depth, reverse=True)))))
    max_right_len, max_left_len = [], []
    # longest string offset per depth
    max_left_len = max(max(len(s) for s, d in zip(lines, depths) if d == depth) for depth in set(depths))

    # same for right
    max_right_len = max(max(len(str(v)) for v, d in zip(values, depths) if d == depth) for depth in set(depths))

    max_right_len += 2
    COLORS = [Style.BRIGHT, Style.BRIGHT, Fore.RESET, Fore.BLACK, Fore.BLACK, Fore.BLACK]
    for i, (v, d) in enumerate(zip(values, depths)):
        _v = v
        _v = f"{COLORS[d]}{v}{Style.RESET_ALL}"
        lines[i] = f"{lines[i].ljust(max_left_len, ' ')}{' ' * (max_right_len - len(str(v)))}{_v}"
    # replace all spaces with non-breaking spaces
    lines = [l.replace(" ", " ") for l in lines]
    return "\n".join(lines)
