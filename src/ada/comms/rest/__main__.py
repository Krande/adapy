"""Entrypoint: `python -m ada.comms.rest`.

Reads settings from env vars, creates the FastAPI app, and runs uvicorn
on the configured host/port.
"""

from __future__ import annotations

import uvicorn

from .config import load_settings


def run() -> None:
    settings = load_settings()
    # Importing as a string lets uvicorn reload-on-change in dev if needed
    # later; here it also keeps create_app from running twice.
    uvicorn.run(
        "ada.comms.rest.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
