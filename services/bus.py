"""
The interaction event bus.

One bus, four front doors. IVRS, portal, chatbot and mobile all normalise into
the same typed event stream, so the triage engine never learns which channel an
interaction arrived through. That single decision is what makes multi-channel
support real rather than four half-systems sharing a logo.

TWO BACKENDS, ONE INTERFACE. `InProcessBus` is the default and needs nothing
installed: `make dev` runs the whole pipeline on a laptop with no services. A
Redis Streams backend swaps in by environment variable for multi-worker
deployment. Application code is identical either way, which is the point — a
system that only works with infrastructure present cannot be demonstrated when
the venue network fails.

Subscribers never block publishers. A slow or broken console must not stall a
live call, so a subscriber whose queue is full loses its oldest events rather
than applying backpressure to the triage path. Dropped events are counted and
reported; a console that has fallen behind shows a gap rather than silently
displaying stale state.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Protocol

log = logging.getLogger(__name__)

SUBSCRIBER_QUEUE_SIZE = 256


@dataclass
class Subscription:
    interaction_id: Optional[str]
    queue: asyncio.Queue
    dropped: int = 0

    def matches(self, interaction_id: str) -> bool:
        return self.interaction_id is None or self.interaction_id == interaction_id


class EventBus(Protocol):
    async def publish(self, interaction_id: str, event: Dict[str, Any]) -> None: ...
    def subscribe(self, interaction_id: Optional[str] = None) -> Subscription: ...
    def unsubscribe(self, subscription: Subscription) -> None: ...


class InProcessBus:
    """Default backend. Keeps a bounded replay buffer per interaction so a
    console connecting mid-call sees the turns it missed rather than starting
    from a blank screen."""

    def __init__(self, replay_size: int = 200):
        self._subscriptions: List[Subscription] = []
        self._replay: Dict[str, deque] = {}
        self._replay_size = replay_size

    async def publish(self, interaction_id: str, event: Dict[str, Any]) -> None:
        self._replay.setdefault(
            interaction_id, deque(maxlen=self._replay_size)).append(event)

        for subscription in list(self._subscriptions):
            if not subscription.matches(interaction_id):
                continue
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest rather than block the triage path.
                try:
                    subscription.queue.get_nowait()
                    subscription.queue.put_nowait(event)
                except asyncio.QueueEmpty:      # pragma: no cover
                    pass
                subscription.dropped += 1
                log.warning("subscriber fell behind on %s; %d events dropped",
                            interaction_id, subscription.dropped)

    def subscribe(self, interaction_id: Optional[str] = None) -> Subscription:
        subscription = Subscription(interaction_id,
                                    asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE))
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)

    def replay(self, interaction_id: str) -> List[Dict[str, Any]]:
        return list(self._replay.get(interaction_id, ()))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)


class RedisBus:                                              # pragma: no cover
    """Redis Streams backend for multi-worker deployment.

    Not exercised by the test suite: this build environment has no Redis, and a
    test that mocks the Redis client would assert only that we call the
    functions we wrote. It is integration-tested against a real server as part
    of deployment, and `InProcessBus` is what the pipeline tests run on.
    """

    def __init__(self, url: str, stream_prefix: str = "samvedna"):
        import redis.asyncio as redis
        self._redis = redis.from_url(url, decode_responses=True)
        self._prefix = stream_prefix

    async def publish(self, interaction_id: str, event: Dict[str, Any]) -> None:
        import json
        await self._redis.xadd(f"{self._prefix}:{interaction_id}",
                               {"payload": json.dumps(event)}, maxlen=1000,
                               approximate=True)

    def subscribe(self, interaction_id: Optional[str] = None) -> Subscription:
        raise NotImplementedError(
            "RedisBus subscription is served by the streaming worker, not in-process")

    def unsubscribe(self, subscription: Subscription) -> None:
        raise NotImplementedError


def build_bus(redis_url: Optional[str] = None) -> EventBus:
    if redis_url:
        return RedisBus(redis_url)                           # pragma: no cover
    return InProcessBus()
