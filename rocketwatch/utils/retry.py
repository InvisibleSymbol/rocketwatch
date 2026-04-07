import inspect
from collections.abc import Callable
from typing import cast

from retry_async.api import EXCEPTIONS
from retry_async.api import retry as __retry


def retry[**P, R](
    exceptions: EXCEPTIONS = Exception,
    *,
    tries: int = -1,
    delay: float = 0,
    max_delay: float | None = None,
    backoff: float = 1,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        return cast(
            Callable[P, R],
            __retry(
                exceptions,
                is_async=inspect.iscoroutinefunction(func),
                tries=tries,
                delay=delay,
                max_delay=max_delay,
                backoff=backoff,
            )(func),
        )

    return decorator
