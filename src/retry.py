"""Retry with exponential backoff, used for connectors, database writes and email."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)

ATTEMPTS = 3
BASE_DELAY_SECONDS = 2.0


class RetryError(Exception):
    """Raised after the final attempt fails. `last` holds the final exception."""

    def __init__(self, label: str, attempts: int, last: BaseException):
        super().__init__(f"{label} failed after {attempts} attempts: {type(last).__name__}: {last}")
        self.label, self.attempts, self.last = label, attempts, last


def with_retry(fn: Callable[[], T], label: str, attempts: int = ATTEMPTS,
               base_delay: float = BASE_DELAY_SECONDS, sleep: Callable[[float], None] = time.sleep,
               on_attempt: Callable[[int], None] | None = None) -> T:
    for attempt in range(1, attempts + 1):
        if on_attempt:
            on_attempt(attempt)
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - any failure is retried
            if attempt == attempts:
                raise RetryError(label, attempts, exc) from exc
            delay = base_delay * 2 ** (attempt - 1)
            log.warning("%s failed (attempt %d/%d, %s); retrying in %.0fs",
                        label, attempt, attempts, type(exc).__name__, delay)
            sleep(delay)
    raise AssertionError("unreachable")
