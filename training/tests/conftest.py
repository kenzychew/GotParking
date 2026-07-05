"""Shared pytest fixtures for the T5 training test suite.

Every Supabase interaction in this test suite is mocked via an in-memory
`FakeSupabaseDB` (tests/fakes.py) or, for the lower-level HTTP client tests,
`httpx.MockTransport` -- httpx's own built-in test seam. No test in this
suite makes a real network call, and none downloads real SINPA data.
"""

from __future__ import annotations
