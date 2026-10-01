"""Очередь заданий worker в Redis (простой список + BLPOP)."""
from __future__ import annotations

import json

QUEUE_KEY = "fashion:jobs"


def enqueue(redis_client, job: dict) -> None:
    redis_client.rpush(QUEUE_KEY, json.dumps(job, default=str))


def dequeue(redis_client, timeout: int = 5) -> dict | None:
    item = redis_client.blpop([QUEUE_KEY], timeout=timeout)
    return json.loads(item[1]) if item else None
