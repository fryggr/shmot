"""Простой лимит запросов на процесс (фиксированное окно в минуту на IP).

Для POC достаточно; при нескольких репликах API лимит переносится в Redis.
"""
from __future__ import annotations

import os
import threading
import time


class RateLimiter:
    def __init__(self, per_minute: int | None = None):
        self.per_minute = per_minute or int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120"))
        self._lock = threading.Lock()
        self._window = 0
        self._counts: dict[str, int] = {}

    def allow(self, key: str) -> bool:
        window = int(time.time() // 60)
        with self._lock:
            if window != self._window:
                self._window, self._counts = window, {}
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key] <= self.per_minute
