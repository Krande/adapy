from __future__ import annotations

import asyncio
import os
import pathlib
import platform
import socket
import sys
import time
from asyncio import Task
from typing import Literal

import websockets

from ada.comms.fb_wrap_model_gen import TargetTypeDC
from ada.config import logger


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


def is_server_running(host="localhost", port=8765) -> bool | Task[bool]:
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
    from ada.comms.wsock.cli import WS_ASYNC_SERVER_PY
    from ada.comms.wsock.server import WebSocketAsyncServer

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

    # In headless CI environments, don't try to spawn external UI/terminals
    no_external_ui = os.getenv("ADA_NO_EXTERNAL_UI", "").lower() in {"1", "true", "yes", "on"}

    if no_external_ui and not run_in_thread:
        # Fall back to background thread mode if we must start a server locally
        run_in_thread = True

    # Note: `is_server_running` may return an asyncio.Task when called from within a running event loop.
    # Use boolean semantics instead of identity comparison to handle that case safely.
    if not is_server_running(host, port):
        if run_in_thread:
            ws = WebSocketAsyncServer(host=host, port=port)
            ws.run_in_background()
        else:
            start_external_ws_server(server_exe, server_args)

        # Wait briefly until the server is reachable
        while not is_server_running(host, port):
            time.sleep(0.1)


def client_as_str(client_type: TargetTypeDC) -> str:
    if client_type == TargetTypeDC.LOCAL:
        return "local"
    elif client_type == TargetTypeDC.WEB:
        return "web"
    else:
        raise ValueError("Invalid client type.")


def client_from_str(client_type: Literal["local", "web"]) -> TargetTypeDC:
    if client_type == "local":
        return TargetTypeDC.LOCAL
    elif client_type == "web":
        return TargetTypeDC.WEB
    else:
        raise ValueError(f"Invalid client type: {client_type}. Expected 'local' or 'web'.")


def start_external_ws_server(server_exe, server_args):
    args = [sys.executable, str(server_exe)]
    if server_args is not None:
        args.extend(server_args)

    args_str = " ".join(args)
    logger.info("Starting server in separate process")
    launch_terminal_with_command(args_str)


def launch_terminal_with_command(command: str):
    import shutil
    import subprocess

    system = platform.system()

    if system == "Windows":
        # This will start a new shell window and run the command
        os.system(f"start cmd.exe /K {command}")
        # subprocess.Popen(["cmd.exe", "/K", command])

    elif system == "Linux":
        # Try common terminal emulators in order of likelihood
        for term in ["gnome-terminal", "konsole", "xfce4-terminal", "lxterminal", "tilix", "x-terminal-emulator"]:
            if shutil.which(term):
                subprocess.Popen([term, "-e", command])
                break
        else:
            raise EnvironmentError("No supported terminal emulator found on Linux.")

    elif system == "Darwin":
        # This will run the command in a new Terminal tab
        apple_script = f"""
        tell application "Terminal"
            activate
            do script "{command}"
        end tell
        """
        subprocess.run(["osascript", "-e", apple_script])

    else:
        raise NotImplementedError(f"Unsupported platform: {system}")
