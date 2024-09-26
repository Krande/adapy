from __future__ import annotations

import asyncio
import os
import pathlib
import socket
import time

import websockets

from ada.comms.fb_model_gen import TargetTypeDC
from ada.config import logger
from ada.visit.deprecated.websocket_server import start_external_ws_server


def is_port_open(host: str, port: int) -> bool:
    """Quickly check if a port is open using a socket connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, socket.timeout):
            return False


async def _check_websocket_server(host: str, port: int) -> bool:
    """Check if a WebSocket server is running by trying to connect."""
    try:
        async with websockets.connect(f"ws://{host}:{port}"):
            logger.info(f"WebSocket server is running on ws://{host}:{port}")
            return True
    except (websockets.exceptions.InvalidURI, OSError, websockets.exceptions.ConnectionClosedError) as e:
        logger.debug(f"Error checking WebSocket server: {e}")
        return False


def is_server_running(host="localhost", port=8765) -> bool:
    """Efficiently check if a WebSocket server is running."""
    if not is_port_open(host, port):
        logger.info(f"Port {port} on host {host} is not open.")
        return False

    loop = asyncio.get_event_loop()
    if loop.is_running():
        # If an asyncio loop is already running, create a new task.
        return asyncio.ensure_future(_check_websocket_server(host, port))
    else:
        # Run the async check if the loop is not running.
        return loop.run_until_complete(_check_websocket_server(host, port))


def start_ws_async_server(
    host="localhost",
    port=8765,
    server_exe: pathlib.Path = None,
    server_args: list[str] = None,
    run_in_thread=False,
    override_binder_check=False,
) -> None:
    from ada.comms.cli_async_ws_server import WS_ASYNC_SERVER_PY
    from ada.comms.wsock_server import WebSocketAsyncServer

    if server_exe is None:
        server_exe = WS_ASYNC_SERVER_PY

    # Check if we are running in a binder environment
    res = os.getenv("BINDER_SERVICE_HOST", None)
    if res is not None and override_binder_check is False:
        logger.info(
            "Running in binder environment, starting server in thread. Pass override_binder_check=True to override"
        )
        logger.warning("Binder does not support websockets, so you will not be able to send data to the viewer")
        run_in_thread = True

    if is_server_running(host, port) is False:
        if run_in_thread:
            ws = WebSocketAsyncServer(host=host, port=port)
            ws.run_in_background()
        else:
            start_external_ws_server(server_exe, server_args)

        while is_server_running(host, port) is False:
            time.sleep(0.1)


def client_as_str(client_type: TargetTypeDC) -> str:
    if client_type == TargetTypeDC.LOCAL:
        return "local"
    elif client_type == TargetTypeDC.WEB:
        return "web"
    else:
        raise ValueError("Invalid client type.")


def client_from_str(client_type: str) -> TargetTypeDC:
    if client_type == "local":
        return TargetTypeDC.LOCAL
    elif client_type == "web":
        return TargetTypeDC.WEB
