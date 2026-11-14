from __future__ import annotations

from d2l_cli.client.errors import D2LError, RateLimitedError


def test_all_errors_are_d2l_errors() -> None:
    import d2l_cli.client.errors as errors_module

    for name in dir(errors_module):
        obj = getattr(errors_module, name)
        if isinstance(obj, type) and issubclass(obj, Exception) and obj is not D2LError:
            assert issubclass(obj, D2LError), f"{name} should inherit from D2LError"


def test_rate_limited_error_carries_retry_after() -> None:
    err = RateLimitedError("rate limited", retry_after_seconds=42)
    assert err.retry_after_seconds == 42
    assert "rate limited" in str(err)


def test_rate_limited_error_retry_after_optional() -> None:
    err = RateLimitedError("rate limited")
    assert err.retry_after_seconds is None
