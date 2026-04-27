"""Entry point for the backend server."""

import logging
import os
import sys

import uvicorn

from backend.infra.config import config
from backend.infra.observability import configure_logging

# Install structured logging before the first ``logger.*`` call so the
# bind-guardrail ERROR lines (which fire before uvicorn.run) come out
# in the configured format. LOG_FORMAT=json and LOG_LEVEL=DEBUG are
# the two knobs.
configure_logging()

logger = logging.getLogger(__name__)

# Bind hosts that don't expose the server beyond the local machine.
# Anything else (``0.0.0.0``, ``::``, a routable IP) is treated as
# "external binding" and triggers the public-bind guardrail below.
_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _enforce_public_bind_guardrail(host: str) -> None:
    """Refuse to start when binding externally without the WS auth token.

    The REST + WebSocket surfaces are unauthenticated by default. Binding
    to ``0.0.0.0`` (or any non-loopback host) on a multi-user / LAN
    machine therefore exposes ``/api/agent/start``, ``/ws`` screenshot
    streams, and the noVNC proxy to anyone who can reach the port.

    The guard requires *both* ``CUA_WS_TOKEN`` (so /ws can't be opened
    by a drive-by page) and an explicit ``CUA_ALLOW_PUBLIC_BIND=1``
    opt-in (so a typo in ``HOST`` can't accidentally publish the
    backend). Either condition missing -> refuse to start with a clear
    error message that names the missing knob.
    """
    if host in _LOOPBACK_BIND_HOSTS:
        return
    allow = os.getenv("CUA_ALLOW_PUBLIC_BIND", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    token = os.getenv("CUA_WS_TOKEN", "").strip()
    if not allow:
        logger.error(
            "Refusing to bind to %r: external binding requires "
            "CUA_ALLOW_PUBLIC_BIND=1 (and CUA_WS_TOKEN). "
            "Use HOST=127.0.0.1 for local development, or set both "
            "envs intentionally to publish the backend.",
            host,
        )
        sys.exit(2)
    if not token:
        logger.error(
            "Refusing to bind to %r: external binding requires "
            "CUA_WS_TOKEN to be set so /ws and /vnc/websockify cannot be "
            "opened without the shared secret. Set CUA_WS_TOKEN and "
            "restart.",
            host,
        )
        sys.exit(2)
    logger.warning(
        "Backend binding externally on %r with CUA_WS_TOKEN set. "
        "Ensure /api/* endpoints are also fronted by auth (reverse "
        "proxy, mTLS, etc.) — REST endpoints are not token-gated.",
        host,
    )


def main():
    """Launch the FastAPI backend via Uvicorn."""
    # ``CUA_RELOAD=1`` opts into hot-reload for development. Debug-level
    # logging is orthogonal and controlled by ``DEBUG``. This avoids the
    # footgun where ``DEBUG=1`` in a prod-ish deploy silently enabled
    # uvicorn reload and hot-swapped worker state.
    reload_enabled = os.getenv("CUA_RELOAD", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    _enforce_public_bind_guardrail(config.host)
    uvicorn.run(
        "backend.server:app",
        host=config.host,
        port=config.port,
        reload=reload_enabled,
        log_level="debug" if config.debug else "info",
        # The frontend sends an application-level heartbeat and reconnects.
        # Uvicorn's protocol ping can produce noisy 1011 keepalive timeouts
        # when a local browser tab or noVNC proxy stalls briefly.
        ws_ping_interval=None,
    )


if __name__ == "__main__":
    main()
