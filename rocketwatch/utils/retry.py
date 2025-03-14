from retry_async import retry as __retry
from functools import partial

retry = partial(__retry, is_async=False)
retry_async = partial(__retry, is_async=True)
