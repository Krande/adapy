import base64
import io
import json
import os
import pathlib
import platform
import sys
import time
from dataclasses import dataclass

import numpy as np
import trimesh

import ada
from ada.api.animations import Animation
from ada.config import logger
from ada.visit.websocket_server import WebSocketServer, start_server

_THIS_DIR = pathlib.Path(__file__).parent
PYGFX_RENDERER_EXE_PY = _THIS_DIR / "rendering" / "render_pygfx.py"
WEBSOCKET_EXE_PY = _THIS_DIR / "websocket_server.py"


@dataclass
class WsRenderMessage:
    data: str  # This will hold the Base64 encoded bytes
    look_at: list[float, float, float] | None = None
    camera_position: list[float, float, float] | None = None


def send_to_viewer(
    part: ada.Part | trimesh.Scene, host="localhost", port=8765, origins: list[str] = None, meta: dict = None
):
    if origins is None:
        send_to_local_viewer(part, host=host, port=port)
    else:
        send_to_web_viewer(part, port=port, origins=origins, meta=meta)


def start_ws_server(
    host="localhost", port=8765, server_exe: pathlib.Path = None, server_args: list[str] = None
) -> WebSocketServer:
    ws = WebSocketServer(host=host, port=port)

    if ws.check_server_running() is False:
        if server_exe is None:
            server_exe = WEBSOCKET_EXE_PY

        args = [sys.executable, str(server_exe)]
        if server_args is not None:
            args.extend(server_args)

        args_str = " ".join(args)
        logger.info("Starting server in separate process")
        # Start the server in a separate process that opens a new shell window
        if platform.system() == "Windows":
            os.system(f"start cmd.exe /K {args_str}")
        elif platform.system() == "Linux":
            os.system(f"xterm -e {args_str}")
        elif platform.system() == "Darwin":
            os.system(f"open -a Terminal.app {args_str}")
        else:
            raise NotImplementedError("Unsupported platform: {}".format(platform.system()))

        while ws.check_server_running() is False:
            time.sleep(0.1)

    return ws


def send_to_ws_server(
    data: str | bytes, host="localhost", port=8765, server_exe: pathlib.Path = None, server_args: list[str] = None
):
    ws = start_ws_server(host=host, port=port, server_exe=server_exe, server_args=server_args)

    ws.send(data)


def send_to_viewer_v2(
    scene: trimesh.Scene,
    tri_anim: Animation = None,
    look_at=None,
    camera_position=None,
    new_gltf_file=None,
    dry_run=False,
):
    if isinstance(look_at, np.ndarray):
        look_at = look_at.tolist()

    if isinstance(camera_position, np.ndarray):
        camera_position = camera_position.tolist()

    with io.BytesIO() as data:
        scene.export(file_obj=data, file_type="glb", buffer_postprocessor=tri_anim)
        msg = WsRenderMessage(
            data=base64.b64encode(data.getvalue()).decode(),
            look_at=look_at,
            camera_position=camera_position,
        )
        if dry_run:
            return None

        send_to_ws_server(json.dumps(msg.__dict__))

        # Optionally save binary data to file
        if new_gltf_file is not None:
            data.seek(0)
            new_gltf_file.parent.mkdir(parents=True, exist_ok=True)
            with open(new_gltf_file, "wb") as f:
                f.write(data.read())


def send_to_local_viewer(part: ada.Part | trimesh.Scene, host="localhost", port=8765):
    """Send a part to the viewer. This will start the viewer if it is not already running."""
    with io.BytesIO() as data:
        start = time.time()
        if isinstance(part, trimesh.Scene):
            part.export(data, file_type="glb")
        else:
            part.to_trimesh_scene().export(data, file_type="glb")
        end = time.time()
        logger.info(f"Exported to glb in {end - start:.2f} seconds")

        send_to_ws_server(data.getvalue(), host=host, port=port, server_exe=PYGFX_RENDERER_EXE_PY)


def send_to_web_viewer(part: ada.Part, port=8765, origins: list[str] = None, meta: dict = None):
    """Send a part to the viewer. This will start the viewer if it is not already running."""

    start = time.time()
    data = io.BytesIO()
    scene = part.to_trimesh_scene()
    scene.metadata["extra_meta"] = meta
    scene.export(data, file_type="glb")

    server_args = ["--port", str(port)]
    if origins is not None:
        server_args.extend(["--origins", ";".join(origins)])
    end = time.time()

    logger.info(f"Exported to glb in {end - start:.2f} seconds")

    send_to_ws_server(data.getvalue(), port=port, server_exe=WEBSOCKET_EXE_PY, server_args=server_args)


if __name__ == "__main__":
    logger.setLevel("INFO")
    start_server()
