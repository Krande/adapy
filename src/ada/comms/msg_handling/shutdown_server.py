from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def shutdown_server_func(server: WebSocketAsyncServer, client: ConnectedClient, message) -> None:
    """Handles a request to shutdown the server."""
    logger.info(f"Shutdown request received from client {client.instance_id}")

    server.server.close()
