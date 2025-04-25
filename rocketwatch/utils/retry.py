from retry_async.api import (
    retry as __retry,
    EXCEPTIONS
)
from typing import Callable, Any


def retry(
    exceptions: EXCEPTIONS = Exception,
    *,
    tries: int = -1,
    delay: float = 0,
    max_delay: float = None,
    backoff: float = 1
) -> Callable[..., Any]:
    return __retry(exceptions, is_async=False, tries=tries, delay=delay, max_delay=max_delay, backoff=backoff)

def retry_async(
    exceptions: EXCEPTIONS = Exception,
    *,
    tries: int = -1,
    delay: float = 0,
    max_delay: float = None,
    backoff: float = 1
) -> Callable[..., Any]:
    return __retry(exceptions, is_async=True, tries=tries, delay=delay, max_delay=max_delay, backoff=backoff)
