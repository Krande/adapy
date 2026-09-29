"""Stopping a LOCAL viewer from its own page.

A viewer started on a workstation (``python -m ada.comms.rest`` by a launcher
script) keeps running after the browser tab closes, and the only way to end it
was to find its process. This module gives such a viewer one route that ends it:
``POST /api/local/shutdown``.

It is opt-in and never present on a deployment:

* The route is registered only when ``ADA_VIEWER_LOCAL_SHUTDOWN_TOKEN`` is set
  AND the server is bound to a loopback address. A deployed image sets neither
  (it binds ``0.0.0.0`` by default), so it has no such route at all: a request
  there is the SPA fallback's 404, not a refusal.
* A request must come from a loopback client and name a loopback ``Host``. The
  second check is what stops DNS rebinding: a page on another site that
  resolves its own name to 127.0.0.1 is same-origin with this server as far as
  the browser is concerned, but it still sends its own host name.
* A request must carry the launch's token in the ``X-Ada-Local-Shutdown``
  header. A custom header also means a cross-origin page cannot send the
  request at all: it needs a CORS preflight, and this server answers none.

The page learns the token from ``GET /api/config`` (``localShutdown.token``),
which is JSON read with ``fetch``: another origin cannot read it. It is never in
``/config.js``, which is a classic script any page may include. ``/config.js``
carries only ``window.ADA_LOCAL_SHUTDOWN = true``, so a UI can decide to show a
"stop" control without a request.

The token does not protect against another program on the same machine: such a
program can read ``/api/config`` too, and could end the process directly anyway.
What it and the checks above protect against is a web page doing it.

Stopping sets uvicorn's ``should_exit`` through the hook ``python -m
ada.comms.rest`` registers (see ``__main__``), so the app's lifespan shutdown
runs as it does on Ctrl+C. Under any other server there is no hook and the route
answers 503 rather than pretend.
"""

from __future__ import annotations

import ipaddress
import secrets
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from ada.config import logger

from .config import Settings

ROUTE = "/api/local/shutdown"
HEADER = "X-Ada-Local-Shutdown"

_hook: Callable[[], None] | None = None


def set_shutdown_hook(hook: Callable[[], None] | None) -> None:
    """What the route calls to end the server; ``None`` removes it."""
    global _hook
    _hook = hook


def is_loopback(host: str | None) -> bool:
    """Whether ``host`` (an address or a name, no port) is this machine."""
    if not host:
        return False
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def enabled(settings: Settings) -> bool:
    """A token was given AND the server listens on loopback only."""
    return bool(settings.local_shutdown_token) and is_loopback(settings.host)


def _host_name(request: Request) -> str:
    host = request.headers.get("host", "")
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def request_is_local(request: Request) -> bool:
    """A loopback client asking for a loopback host name."""
    client = request.client.host if request.client else None
    return is_loopback(client) and is_loopback(_host_name(request))


def config_entry(settings: Settings, request: Request) -> dict:
    """``/api/config``'s ``localShutdown``: the token, only for a local page."""
    if not enabled(settings):
        return {"available": False}
    if not request_is_local(request):
        return {"available": False}
    return {"available": True, "token": settings.local_shutdown_token, "header": HEADER}


def install(app: FastAPI, settings: Settings) -> None:
    """Register the route, when this server may have it."""
    if not enabled(settings):
        return

    @app.post(ROUTE, include_in_schema=False)
    async def local_shutdown(request: Request) -> JSONResponse:
        if not request_is_local(request):
            raise HTTPException(status_code=403, detail="only a local page may stop the local viewer")
        given = request.headers.get(HEADER, "")
        if not given or not secrets.compare_digest(given, settings.local_shutdown_token):
            raise HTTPException(status_code=403, detail="wrong or missing local shutdown token")
        hook = _hook
        if hook is None:
            raise HTTPException(
                status_code=503,
                detail="this server was not started by `python -m ada.comms.rest`; stop it where it runs",
            )
        logger.info("local viewer: shutdown requested from the page")
        # After the reply has gone: stopping first would cut it off.
        return JSONResponse({"stopping": True}, status_code=202, background=BackgroundTask(hook))
