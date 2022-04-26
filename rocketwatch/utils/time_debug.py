import functools
import logging
import time

from utils.cfg import cfg

log = logging.getLogger("time_debug")
log.setLevel(cfg["log_level"])


def timerun(func):
    """ Calculate the execution time of a method and return it back"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start

        log.debug(f"{func.__name__} took {duration} seconds")
        return result

    return wrapper
