import logging
import math

from utils.cfg import cfg
from utils.shared_w3 import w3

# TODO change to merge timestamp once known
MERGE_TIMESTAMP = math.inf

log = logging.getLogger("get_nearest_block")
log.setLevel(cfg["log_level"])


def _get_timestamp(block):
    return w3.eth.getBlock(block)['timestamp']


def get_block_by_timestamp(timestamp):
    history = []
    error_map = {}
    i_pre = 1
    i_latest = w3.eth.get_block('latest')['number']
    i_post = i_latest
    log.debug(f"Looking for block with timestamp {timestamp}")
    while (i_pre, i_post) not in history:
        log.debug(f'Searching between blocks {i_pre} and {i_post}')
        if i_post == i_pre:
            break
        t0, t1 = _get_timestamp(i_pre), _get_timestamp(i_post)
        av_block_time = (t1 - t0) / (i_post - i_pre)

        # if block-times were evenly-spaced, get expected block number
        k = (timestamp - t0) / (t1 - t0)
        i_expected = round(i_pre + k * (i_post - i_pre))
        # sanitize expected block number
        i_expected = max(1, min(i_expected, i_latest))
        if i_expected in history:
            break

        # get the ACTUAL time for that block
        t_expected = _get_timestamp(i_expected)

        error = (timestamp - t_expected) / av_block_time
        error_map[i_expected] = error
        log.debug(
            f"Estimated Block {i_expected} with timestamp {t_expected} is off {timestamp - t_expected}s ({error:=.3f} Blocks)")

        # if the block before this one has a positive error, and we currently have a negative error,
        # then we know we have overshot the target
        if i_expected - 1 in error_map and error_map[i_expected - 1] > 0 and error < 0:
            log.debug(
                'Overshot target, previous Block is behind target, current Block is ahead'
            )

            break

        # if the block after this one has a negative error, and we currently have a positive error,
        # then we know we have undershot the target
        if i_expected + 1 in error_map and error_map[i_expected + 1] < 0 and error > 0:
            log.debug(
                'Undershot target, next Block is ahead of target, current Block is behind'
            )

            break

        if error == 0:
            log.debug(f"Block {i_expected} matches timestamp {timestamp}")
            return i_expected, len(history)

        if i_expected not in history:
            history.append(i_expected)

        i_expected_adj = i_expected + error

        r = abs(error)

        i_pre = max(1, math.floor(i_expected_adj - r))
        i_post = min(math.ceil(i_expected_adj + r), i_latest)
    # find the block with the smallest error in the error_map
    best_guess = min(error_map.items(), key=lambda x: abs(x[1]))
    log.debug(f"Closest Block is {best_guess[0]} with error {best_guess[1]:=.3f}")
    return best_guess[0], len(history)
