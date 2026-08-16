import random
import time
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status in {429, 500, 502, 503, 504}:
        return True
    text = str(error).lower()
    return "timeout" in text or "connection reset" in text


def with_retry(operation: Callable[[], T], max_retries: int = 3) -> T:
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= max_retries or not is_retryable(exc):
                raise
            delay = min(2**attempt, 30) + random.random()
            print(f"模型请求失败，{delay:.1f} 秒后重试：{exc}")
            time.sleep(delay)

    raise RuntimeError("unreachable")


def is_prompt_too_long(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "prompt too long",
            "maximum context length",
            "context_length_exceeded",
        )
    )