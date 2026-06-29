from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        secret: str,
        *,
        limit: int = 8,
        window: float = 300,
        global_limit: int = 200,
        max_keys: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.secret = secret.encode()
        self.limit = limit
        self.window = window
        self.global_limit = global_limit
        self.max_keys = max_keys
        self.clock = clock
        self.events: OrderedDict[str, deque[float]] = OrderedDict()
        self.global_events: deque[float] = deque()

    def key(self, value: str) -> str:
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def allow(self, operation: str, email: str, client_ip: str) -> bool:
        now = self.clock()
        cutoff = now - self.window
        while self.global_events and self.global_events[0] <= cutoff:
            self.global_events.popleft()
        if len(self.global_events) >= self.global_limit:
            return False
        keys = (f"{operation}:e:{self.key(email)}", f"{operation}:i:{self.key(client_ip)}")
        buckets: list[deque[float]] = []
        for key in keys:
            bucket = self.events.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            buckets.append(bucket)
            self.events.move_to_end(key)
        for bucket in buckets:
            bucket.append(now)
        self.global_events.append(now)
        while len(self.events) > self.max_keys:
            self.events.popitem(last=False)
        return True
