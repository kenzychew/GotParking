"""Shared pytest fixtures for the T5 training test suite.

Every Supabase interaction in this test suite is mocked via an in-memory
`FakeSupabaseDB` (tests/fakes.py) or, for the lower-level HTTP client tests,
`httpx.MockTransport` -- httpx's own built-in test seam. No test in this
suite makes a real network call, and none downloads real SINPA data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx
import numpy as np
import pytest

#: Type aliases for the two transport-factory fixtures below, so test files
#: importing them for type hints don't need to repeat the full nested
#: Callable[...] spelling (and stay under the 100-char line limit).
SequentialTransportFactory = Callable[
    [Sequence[httpx.Response | BaseException]], httpx.MockTransport
]
RoutedTransportFactory = Callable[
    [Callable[[httpx.Request], httpx.Response]], httpx.MockTransport
]


def _sequential_handler(
    responses: Sequence[httpx.Response | BaseException],
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a MockTransport handler that replays responses/errors in order.

    Each call to the transport pops the next item off ``responses``; if the
    item is an exception instance, it is raised instead of returned. Used
    to simulate "fails once, then succeeds on retry" scenarios and
    multi-page pagination sequences.
    """
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if not remaining:
            raise AssertionError(
                f"sequential transport ran out of queued responses "
                f"(unexpected extra request: {request.method} {request.url})"
            )
        item = remaining.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return handler


@pytest.fixture
def make_sequential_transport() -> SequentialTransportFactory:
    """Factory fixture: build a MockTransport that replays a fixed sequence
    of responses (or raised exceptions), one per call, in order.
    """

    def factory(responses: Sequence[httpx.Response | BaseException]) -> httpx.MockTransport:
        return httpx.MockTransport(_sequential_handler(responses))

    return factory


@pytest.fixture
def make_routed_transport() -> RoutedTransportFactory:
    """Factory fixture: build a MockTransport from an arbitrary handler.

    Use this (instead of `make_sequential_transport`) when a test needs to
    branch on request method/URL/params rather than replay a fixed list.
    """

    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    return factory


@pytest.fixture(scope="session")
def tiny_lightgbm_model_str() -> str:
    """Train a minimal real LightGBM regressor and return its text dump.

    Exercises the actual `lightgbm.Booster(model_str=...)` round trip (not
    just mocked logic) without shipping a binary fixture file. Trained once
    per test session -- deterministic (fixed seed) and read-only from every
    consumer's point of view, so session scope is safe.

    Columns follow the exact feature contract order: [dow, slot_of_day,
    is_holiday, lots_now, lots_15m_ago, lots_30m_ago, lots_60m_ago].
    """
    import lightgbm as lgb

    rng = np.random.default_rng(42)
    n = 50
    features = np.column_stack(
        [
            rng.integers(0, 7, n),
            rng.integers(0, 96, n),
            rng.integers(0, 2, n),
            rng.integers(0, 200, n),
            rng.integers(0, 200, n),
            rng.integers(0, 200, n),
            rng.integers(0, 200, n),
        ]
    ).astype(np.float64)
    target = features[:, 3] + rng.normal(0, 1, n)

    dataset = lgb.Dataset(features, label=target)
    params = {
        "objective": "regression",
        "verbosity": -1,
        "min_data_in_leaf": 1,
        "min_data_in_bin": 1,
        "num_leaves": 3,
    }
    booster = lgb.train(params, dataset, num_boost_round=2)
    return booster.model_to_string()
