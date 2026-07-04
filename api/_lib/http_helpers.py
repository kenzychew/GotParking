"""HTTP-layer glue between Vercel's BaseHTTPRequestHandler and the pure
``_lib`` orchestration functions (``batch_logic.py`` / ``read_logic.py``).

Keeping this thin and separate from the business logic means
`handle_batch_predict`/`handle_forecast_read` are testable as plain
function calls (headers/deps in, HttpResponse out) without needing a real
socket or a live BaseHTTPRequestHandler instance.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HttpResponse:
    """A framework-agnostic HTTP response.

    Attributes:
        status: HTTP status code.
        body: JSON-serializable response body.
        headers: Extra response headers. ``Content-Type`` is added
            automatically by :func:`write_http_response` if not already
            present.
    """

    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


def get_header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitively look up a header value.

    Works both for a plain ``dict`` (as used in tests) and for
    ``http.client.HTTPMessage`` (``BaseHTTPRequestHandler.headers`` in
    production), which also exposes ``.items()``.

    Args:
        headers: A header-name -> value mapping.
        name: The header name to look up (any case).

    Returns:
        The header value, or None if not present.
    """
    name_lower = name.lower()
    for key, value in headers.items():
        if key.lower() == name_lower:
            return value
    return None


def write_http_response(handler: BaseHTTPRequestHandler, response: HttpResponse) -> None:
    """Serialize an :class:`HttpResponse` onto a live request handler.

    Args:
        handler: The active request handler (provides ``send_response``,
            ``send_header``, ``end_headers``, and a ``wfile`` to write to).
        response: The response to send.
    """
    payload = json.dumps(response.body).encode("utf-8")
    headers = dict(response.headers)
    headers.setdefault("Content-Type", "application/json")

    handler.send_response(response.status)
    for key, value in headers.items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def unexpected_error_response(status: int = 500) -> HttpResponse:
    """Build a safe, well-formed fallback response for unexpected errors.

    Used as the last line of defense in each endpoint's top-level
    try/except, so an unanticipated exception still produces a well-formed
    JSON response instead of a raw/broken connection ("never raise a raw
    500", stated repeatedly across the design doc's Failure Modes registry).

    Args:
        status: The HTTP status to report.

    Returns:
        A generic typed-error HttpResponse.
    """
    return HttpResponse(
        status,
        {"error": "internal_error", "message": "Unexpected server error"},
        {"Content-Type": "application/json"},
    )
