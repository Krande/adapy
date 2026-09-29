"""Entrypoint: `python -m ada.comms.rest`.

Reads settings from env vars, creates the FastAPI app, and runs uvicorn
on the configured host/port.
"""

from __future__ import annotations

import threading

import uvicorn

from . import local_shutdown
from .config import load_settings

#: How long a stop requested by the page waits for open connections (a
#: streaming response, a long poll) before uvicorn drops them.
LOCAL_SHUTDOWN_GRACE_S = 5.0


def run() -> None:
    settings = load_settings()
    # Importing as a string lets uvicorn reload-on-change in dev if needed
    # later; here it also keeps create_app from running twice.
    config = uvicorn.Config(
        "ada.comms.rest.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    # A Server object rather than ``uvicorn.run`` (which builds the same one),
    # so the local shutdown route has something to stop: ``should_exit`` is
    # what Ctrl+C sets, and the app's lifespan shutdown runs after it.
    server = uvicorn.Server(config)

    def stop() -> None:
        server.should_exit = True
        # An open connection would otherwise keep a stopped viewer alive.
        timer = threading.Timer(LOCAL_SHUTDOWN_GRACE_S, setattr, args=(server, "force_exit", True))
        timer.daemon = True
        timer.start()

    local_shutdown.set_shutdown_hook(stop)
    server.run()


if __name__ == "__main__":
    run()
