import argparse
import asyncio
import os
import pathlib
import sys
import time

from ada.comms.wsock.server import WebSocketAsyncServer
from ada.config import logger

WS_ASYNC_SERVER_PY = pathlib.Path(__file__)


async def start_async_server(host="localhost", port=8765, run_in_thread=False, log_level="DEBUG"):
    logger.setLevel(log_level)
    is_debug = False
    if log_level == "DEBUG":
        is_debug = True

    server = WebSocketAsyncServer(host, port, debug=is_debug)
    if run_in_thread:
        await server.run_in_background()
    else:
        await server.start_async()


def start_async_ws_server(host="localhost", port=8765, run_in_thread=False):
    asyncio.run(start_async_server(host, port, run_in_thread))


def ws_async_cli_app():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--origins", type=str, default="localhost")
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--open-viewer", action="store_true")
    parser.add_argument("--log-level", type=str, default="DEBUG")
    args = parser.parse_args()

    origins_list = []
    for origin in args.origins.split(";"):
        if origin == "localhost":
            origins_list.append("http://localhost:5173")  # development server
            for i in range(8888, 8899):  # local jupyter servers
                origins_list.append(f"http://localhost:{i}")
            origins_list.append("null")  # local html
        else:
            origins_list.append(origin)

    if args.open_viewer:
        from ada.visit.rendering.renderer_react import RendererReact

        RendererReact().show()

    asyncio.run(start_async_server(args.host, args.port, log_level=args.log_level))


def ensure_ws_server(host: str = "localhost", port: int = 13579, wait_seconds: float = 3.0) -> bool:
    """
    Ensure a single background websocket relay server is running.

    Returns True if a server is already running or was successfully started; False otherwise.
    """
    if ping_ws_server(host, port):
        logger.debug(f"WebSocket server already running on {host}:{port}")
        return True

    # Spawn a detached background process: python -m paradoc.frontend.ws_server --host ... --port ...
    cmd = [sys.executable, "-m", "paradoc.frontend.ws_server", "--host", host, "--port", str(port)]
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
        import subprocess

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


if __name__ == "__main__":
    ws_async_cli_app()
