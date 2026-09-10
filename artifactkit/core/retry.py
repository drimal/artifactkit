"""Retry with exponential backoff, used only for the destination write
step. Render and validation are deliberately never retried: a bad spec
produces the same failure every time, retrying just wastes time and
hides the real problem."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter: bool = True


def retry_with_backoff(
    fn: Callable[[], T],
    policy: RetryPolicy,
    is_retryable: Callable[[Exception], bool],
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    """Runs fn(), retrying on exceptions is_retryable accepts, up to
    policy.max_attempts total attempts. Re-raises the last exception
    once attempts are exhausted, or immediately if is_retryable(exc)
    is False."""
    last_exc: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, filtered by is_retryable
            if not is_retryable(exc) or attempt == policy.max_attempts:
                raise
            last_exc = exc
            delay = min(
                policy.base_delay_seconds * (2 ** (attempt - 1)),
                policy.max_delay_seconds,
            )
            if policy.jitter:
                delay *= random.uniform(0.5, 1.0)
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(delay)
    # Unreachable: the loop always returns or raises. Satisfies type checkers.
    assert last_exc is not None
    raise last_exc
