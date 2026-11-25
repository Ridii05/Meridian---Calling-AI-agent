import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        envelope = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        dead: list[asyncio.Queue] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                dead.append(queue)
        for q in dead:
            self._subscribers.discard(q)

    async def subscribe(self) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        try:
            yield f"data: {json.dumps({'type': 'connected', 'payload': {}})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # SSE keepalive comment
                    yield ": keepalive\n\n"
        finally:
            self._subscribers.discard(queue)


event_bus = EventBus()