from __future__ import annotations

import asyncio
import os
import pathlib
import platform
import socket
import subprocess
import sys
import time
from asyncio import Task
from typing import Literal

import websockets

from ada.comms.fb_wrap_model_gen import TargetTypeDC
from ada.config import logger


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Quickly check if a port is open using a TCP connection.
    
    Uses socket.create_connection() which is a safer and cleaner approach
    that handles socket creation and connection in one call with proper cleanup.
    
    Note: This uses a raw TCP socket which will cause 'opening handshake failed' errors
    on the WebSocket server side. This is expected and harmless - it's just a quick
    check to see if anything is listening on the port before attempting a full
    WebSocket connection.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            logger.debug(f"Successfully connected to WebSocket server via TCP on {host}:{port}")
            return True
    except Exception as e:
        logger.debug(f"Failed to connect via TCP to {host}:{port}: {e}")
        return False


def ping_ws_server(host: str, port: int) -> bool:
    """Check if a WebSocket server is running and responding."""
    return is_port_open(host, port)


def ensure_ws_server(host: str = "localhost", port: int = 8765, wait_seconds: float = 3.0) -> bool:
    """
    Ensure a single background websocket server is running.

    Spawns a fully detached background process that will persist independently
    of the parent process. On Windows, uses DETACHED_PROCESS and CREATE_NEW_PROCESS_GROUP
    flags. On Unix, uses start_new_session to properly detach.

    Returns True if a server is already running or was successfully started; False otherwise.
    """
    if ping_ws_server(host, port):
        logger.debug(f"WebSocket server already running on {host}:{port}")
        return True

    # Spawn a detached background process: python -m ada.comms.wsock.cli --host ... --port ...
    cmd = [sys.executable, "-m", "ada.comms.wsock.cli", "--host", host, "--port", str(port)]
    logger.info(f"Starting WebSocket server with command: {' '.join(cmd)}")

    # On Windows, detach the process using creationflags
    # On Unix, use start_new_session to properly detach
    creationflags = 0
    start_new_session = False

    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP (0x200) | DETACHED_PROCESS (0x8)
        creationflags = 0x00000200 | 0x00000008
    else:
        # On Unix systems, start a new session to detach from parent
        start_new_session = True

    try:
        with open(os.devnull, "wb") as devnull:
            proc = subprocess.Popen(
                cmd,
                stdout=devnull,
                stderr=devnull,
                stdin=devnull,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
            logger.info(f"WebSocket server process started with PID: {proc.pid}")
    except Exception as e:
        logger.error(f"Failed to start WebSocket server: {e}", exc_info=True)
        return False

    # Wait briefly for it to boot and become pingable
    deadline = time.time() + wait_seconds
    attempts = 0
    while time.time() < deadline:
        if ping_ws_server(host, port):
            logger.info(f"WebSocket server successfully started and responding on {host}:{port}")
            return True
        attempts += 1
        time.sleep(0.1)

    logger.error(f"WebSocket server failed to start after {wait_seconds}s and {attempts} ping attempts")
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
    run_in_thread=True,
    override_binder_check=False,
) -> None:
    """
    Ensure a WebSocket server is running, starting one if necessary.
    
    By default, uses `ensure_ws_server` which spawns a fully detached background process
    that persists independently of the parent process.
    
    Args:
        host: The host address for the WebSocket server.
        port: The port for the WebSocket server.
        server_exe: Optional path to server executable (used only for terminal launch mode).
        server_args: Optional arguments for the server (used only for terminal launch mode).
        run_in_thread: If True, run the server in a background thread instead of a detached process.
        override_binder_check: If True, skip binder environment detection.
    """
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

    # Check if server is already running
    if ping_ws_server(host, port):
        logger.debug(f"WebSocket server already running on {host}:{port}")
        return

    if run_in_thread:
        # Use fully detached background process (default mode)
        ensure_ws_server(host, port)
    else:
        # Alternatively, launch in a new terminal window (good for debugging)
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
