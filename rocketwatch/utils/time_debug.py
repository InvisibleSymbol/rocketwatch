import functools
import logging
import time

from utils.cfg import cfg

log = logging.getLogger("time_debug")
log.setLevel(cfg["log_level"])


def timerun(func):
    """Measure and log the execution time of a method"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start

        log.debug(f"{func.__name__} took {duration} seconds")
        return result

    return wrapper


def timerun_async(func):
    """Measure and log the execution time of an async method"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.time()
        result = await func(*args, **kwargs)
        duration = time.time() - start

        log.debug(f"{func.__name__} took {duration} seconds")
        return result

    return wrapper
