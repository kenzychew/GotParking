"""Healthchecks.io /fail ping helper for batch predict's hard failures.

The env var HEALTHCHECKS_TRAINING_PING_URL is the training job's
healthchecks.io check. It is reused here for batch predict's own hard
failures (model artifact missing/corrupt, or a Supabase read/write failure
surviving the single retry) because this lane was wired only one ping URL,
not a separate batch-predict check. Each ping's `reason` string
distinguishes the two cases so the owner's alert email is not ambiguous
about which subsystem actually failed (see config.py's FAIL_REASON_*
constants). Pinging is always best-effort: skipped (logged, not raised)
when the env var is unset, and never allowed to raise out of an
already-failing request path.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_PING_TIMEOUT_SECONDS = 5.0


def fire_fail_ping(
    ping_base_url: str | None,
    reason: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """POST a failure reason to ``{ping_base_url}/fail``.

    Args:
        ping_base_url: The healthchecks.io check's base ping URL (no
            trailing path segment), or None/empty to skip pinging entirely.
        reason: A short machine-readable reason string (see config.py's
            ``FAIL_REASON_*`` constants), sent as the raw request body --
            healthchecks.io logs up to 10KB of the ping body as diagnostic
            info.
        transport: Optional httpx transport override for tests.

    Note:
        This function never raises. A network failure while pinging is
        logged at WARNING and swallowed -- failing to alert must not itself
        become an unhandled exception in a request path that is already
        handling a failure.
    """
    if not ping_base_url:
        logger.info("healthchecks ping URL unset; skipping /fail ping (reason=%s)", reason)
        return

    url = f"{ping_base_url.rstrip('/')}/fail"
    try:
        with httpx.Client(transport=transport, timeout=_PING_TIMEOUT_SECONDS) as client:
            client.post(url, content=reason.encode("utf-8"))
        logger.info("healthchecks /fail ping sent (reason=%s)", reason)
    except httpx.HTTPError as exc:
        logger.warning("healthchecks /fail ping failed (reason=%s): %s", reason, exc)
