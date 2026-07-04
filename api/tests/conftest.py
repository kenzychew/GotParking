"""Shared pytest fixtures for the T4 test suite.

Every Supabase interaction in this test suite is mocked via
``httpx.MockTransport`` -- httpx's own built-in test seam -- rather than a
separate HTTP-mocking library, per the "mock httpx ... responses"
instruction. No test in this suite makes a real network call.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx
import pytest


def _sequential_handler(
    responses: Sequence[httpx.Response | BaseException],
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a MockTransport handler that replays responses/errors in order.

    Each call to the transport pops the next item off ``responses``; if the
    item is an exception instance, it is raised instead of returned. This is
    used to simulate "fails once, then succeeds on retry" scenarios and
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
def make_sequential_transport() -> (
    Callable[[Sequence[httpx.Response | BaseException]], httpx.MockTransport]
):
    """Factory fixture: build a MockTransport that replays a fixed sequence
    of responses (or raised exceptions), one per call, in order.
    """

    def factory(responses: Sequence[httpx.Response | BaseException]) -> httpx.MockTransport:
        return httpx.MockTransport(_sequential_handler(responses))

    return factory


@pytest.fixture
def make_routed_transport() -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.MockTransport]:
    """Factory fixture: build a MockTransport from an arbitrary handler.

    Use this (instead of `make_sequential_transport`) when a test needs to
    branch on request method/URL/params rather than replay a fixed list.
    """

    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    return factory
