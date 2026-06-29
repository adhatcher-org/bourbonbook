from __future__ import annotations

from bourbonbook.rate_limit import RateLimiter


def test_rate_limiter_bounds_keys_and_expires() -> None:
    now = [0.0]
    limiter = RateLimiter(
        "secret", limit=2, window=10, global_limit=100, max_keys=4, clock=lambda: now[0]
    )
    assert limiter.allow("login", "one@example.com", "1.2.3.4")
    assert limiter.allow("login", "one@example.com", "1.2.3.4")
    assert not limiter.allow("login", "one@example.com", "1.2.3.4")
    for index in range(10):
        limiter.allow("register", f"{index}@example.com", f"10.0.0.{index}")
    assert len(limiter.events) <= 4
    now[0] = 11
    assert limiter.allow("login", "one@example.com", "1.2.3.4")
    assert "one@example.com" not in " ".join(limiter.events)
