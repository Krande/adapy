import io
import os
import pathlib
import platform
import subprocess
import sys
import time
import trimesh

import ada
from ada.config import logger
from ada.visit.websocket_server import is_server_running, start_server

RENDERER_EXE_PY = pathlib.Path(__file__).parent / "render_pygfx.py"
WEBSOCKET_EXE_PY = pathlib.Path(__file__).parent / "websocket_server.py"


def send_to_viewer(part: ada.Part | trimesh.Scene, host="localhost", port=8765, origins: list[str] = None, meta: dict = None):
    if origins is None:
        send_to_local_viewer(part, host=host, port=port)
    else:
        send_to_web_viewer(part, port=port, origins=origins, meta=meta)


def send_to_local_viewer(part: ada.Part | trimesh.Scene, host="localhost", port=8765):
    """Send a part to the viewer. This will start the viewer if it is not already running."""
    from ada.visit.websocket_server import WebSocketServer

    ws = WebSocketServer(host=host, port=port)
    if ws.check_server_running() is False:
        logger.info("Starting server in separate process")
        # Start the server in a separate process that opens a new shell window
        if platform.system() == "Windows":
            os.system("start cmd.exe /K {} {}".format(sys.executable, str(RENDERER_EXE_PY)))
        elif platform.system() == "Linux":
            os.system("xterm -e {} {}".format(sys.executable, str(RENDERER_EXE_PY)))
        elif platform.system() == "Darwin":
            os.system("open -a Terminal.app {} {}".format(sys.executable, str(RENDERER_EXE_PY)))
        else:
            raise NotImplementedError("Unsupported platform: {}".format(platform.system()))

        while ws.check_server_running() is False:
            time.sleep(0.1)

    with io.BytesIO() as data:
        start = time.time()
        if isinstance(part, trimesh.Scene):
            part.export(data, file_type="glb")
        else:
            part.to_trimesh_scene().export(data, file_type="glb")
        end = time.time()
        logger.info(f"Exported to glb in {end - start:.2f} seconds")
        ws.send(data.getvalue())


def send_to_web_viewer(part: ada.Part, port=8765, origins: list[str] = None, meta: dict = None):
    """Send a part to the viewer. This will start the viewer if it is not already running."""
    from websockets.sync.client import connect

    if is_server_running(port=port) is False:
        logger.info("Starting server in separate process")
        # Start the server in a separate process that opens a new shell window (on Windows)
        args = ["start", "cmd.exe", "/K", sys.executable, str(WEBSOCKET_EXE_PY), f"--port={port}"]
        if origins is not None:
            args.append(f"--origins={';'.join(origins)}")

        subprocess.Popen(args, shell=True)
        while is_server_running(port=port) is False:
            time.sleep(0.1)

    start = time.time()
    data = io.BytesIO()
    scene = part.to_trimesh_scene()
    scene.metadata["extra_meta"] = meta
    scene.export(data, file_type="glb")

    end = time.time()
    logger.info(f"Exported to glb in {end - start:.2f} seconds")

    with connect(f"ws://localhost:{port}") as websocket:
        websocket.send(data.getvalue())


if __name__ == "__main__":
    logger.setLevel("INFO")
    start_server()
