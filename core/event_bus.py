"""
Janus — простой pub/sub event bus на asyncio.Queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict

log = logging.getLogger("janus.events")


class EventBus:
    """Broadcast events to all SSE subscribers."""

    def __init__(self):
        self._subscribers: Dict[int, asyncio.Queue] = {}
        self._counter = 0

    async def publish(self, event_type: str, data: Any):
        """Send event to every active subscriber."""
        payload = json.dumps(data, default=str)
        dead = []
        for sid, q in self._subscribers.items():
            try:
                q.put_nowait((event_type, payload))
            except asyncio.QueueFull:
                dead.append(sid)
        for sid in dead:
            self._subscribers.pop(sid, None)

    async def subscribe(self) -> AsyncGenerator[tuple[str, str], None]:
        """Yields (event_type, json_data) tuples."""
        self._counter += 1
        sid = self._counter
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[sid] = q
        try:
            while True:
                event_type, payload = await q.get()
                yield event_type, payload
        finally:
            self._subscribers.pop(sid, None)

    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Global singleton
event_bus = EventBus()

