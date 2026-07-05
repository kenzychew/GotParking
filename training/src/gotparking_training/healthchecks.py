"""healthchecks.io dead-man's-switch ping helpers for the weekly training job.

Design doc reference: Premise #8 (absence-based alerting). The weekly
training job pings its own healthchecks.io check on successful completion
(a bare GET) -- this catches not only crashes but the job never running at
all (e.g. GitHub auto-disabling a scheduled workflow after 60 days of repo
inactivity), which a non-zero-exit alert structurally cannot see. Hard
failures (crash, Storage upload failure surviving its retry) instead POST a
short reason to the check's `/fail` endpoint.

Pinging is always best-effort: skipped (logged, not raised) when the env
var is unset, and never allowed to raise out of an already-failing run --
failing to alert must not itself become an unhandled exception.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_PING_TIMEOUT_SECONDS = 10.0


def ping_success(ping_base_url: str | None, *, transport: httpx.BaseTransport | None = None
                  ) -> None:
    """Send a bare success ping to healthchecks.io on successful completion.

    Args:
        ping_base_url: The healthchecks.io check's base ping URL, or
            None/empty to skip pinging entirely.
        transport: Optional httpx transport override for tests.

    Note:
        Never raises. A network failure while pinging is logged at WARNING
        and swallowed.
    """
    if not ping_base_url:
        logger.info("healthchecks ping URL unset; skipping success ping")
        return

    try:
        with httpx.Client(transport=transport, timeout=_PING_TIMEOUT_SECONDS) as client:
            client.get(ping_base_url)
        logger.info("healthchecks success ping sent")
    except httpx.HTTPError as exc:
        logger.warning("healthchecks success ping failed: %s", exc)


def ping_fail(
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
        Never raises. A network failure while pinging is logged at WARNING
        and swallowed -- failing to alert must not itself become an
        unhandled exception in a run that is already handling a failure.
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
