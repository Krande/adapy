import asyncio
import base64
import io
import json
import os
import pathlib
import platform
import re
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from multiprocessing import Queue

import numpy as np
import trimesh
import websockets

import ada
from ada.config import logger

_THIS_DIR = pathlib.Path(__file__).parent
WEBSOCKET_EXE_PY = _THIS_DIR / "websocket_cli.py"


class SceneAction(str, Enum):
    NEW = "new"
    REPLACE = "replace"
    ADD = "add"
    REMOVE = "remove"


def pretty_print_ports(client_origins):
    split_origins = defaultdict(list)
    for item in client_origins:
        result = re.search(r"(?P<protocol>\w+):\/\/(?P<host>[\w\.]+):(?P<port>\d+)", item)
        if result is None:
            continue
        d = result.groupdict()
        port = int(d.get("port"))
        host = d.get("host")
        split_origins[host].append(port)

    result = []
    for key, values in split_origins.items():
        values.sort()
        ranges = [[values[0]]]
        for v in values[1:]:
            if v == ranges[-1][-1] + 1:
                ranges[-1].append(v)
            else:
                ranges.append([v])
        range_strs = [f"{r[0]}:{r[-1]}" if len(r) > 1 else str(r[0]) for r in ranges]
        result.append(f"- {key}: [{', '.join(range_strs)}]")
    return "\n" + "\n".join(result)


@dataclass
class WebSocketServer:
    client_origins: list[str] = field(default_factory=list)
    clients: dict = field(default_factory=dict)
    port: int = 8765
    host: str = "localhost"
    message_queue: Queue = field(default_factory=Queue)
    debug_mode: bool = False
    _ping_sender: websockets.WebSocketServerProtocol | None = None

    def check_server_running(self):
        return is_server_running(self.host, self.port)

    async def handler(self, websocket: websockets.WebSocketServerProtocol):
        """This will handle the connection of a new client"""

        if websocket.origin in self.client_origins:
            if websocket.origin in self.clients.keys():
                logger.debug(f"Client re-connected from origin {websocket.origin}")
            else:
                logger.debug(f"Client connected from origin {websocket.origin}")
            self.clients[websocket.origin] = websocket

            if self.message_queue is not None and not self.message_queue.empty():
                logger.debug("Sending cached data to client")
                await websocket.send(self.message_queue.get())
        try:
            async for message in websocket:
                if message == "ping":
                    self._ping_sender = websocket
                await self.update_clients(message, websocket.origin)
        except websockets.exceptions.ConnectionClosedError:
            logger.debug(f"Client {websocket.origin} disconnected")
            # self.clients.pop(websocket.origin)

    async def update_clients(self, data, origin):
        """This will update all clients with the latest data"""
        logger.debug(f"Received data from {origin}")
        logger.debug(f"Active clients: {self.clients.keys()}")
        logger.info(f"Updating {len(self.clients)} clients")

        # if 0 clients, cache the data
        if len(self.clients.keys()) == 0:
            logger.debug("No clients connected, caching data")
            self.message_queue.put(data)
            return

        for client_origin, client in self.clients.items():
            logger.debug(f"Client {client_origin} is open: {client.open}")
            if data == "pong" and self._ping_sender is not None:
                try:
                    await self._ping_sender.send(data)
                except websockets.exceptions.ConnectionClosedError:
                    logger.debug("Ping sender is closed")
                self._ping_sender = None
            if client_origin == origin:
                continue
            if client.open and client.origin in self.clients:
                logger.debug(f"Sending data to {client_origin}")
                await client.send(data)

    async def server_start_main(self):
        async with websockets.serve(self.handler, self.host, self.port, max_size=10**9):
            await asyncio.Future()  # run forever

    def start(self):
        if self.debug_mode:
            logger.setLevel("DEBUG")
        else:
            logger.setLevel("INFO")

        # pretty printed string of origins
        result_str = pretty_print_ports(self.client_origins)
        logger.info(f"Starting server {self.host}:{self.port} with accepted origins {result_str}")
        asyncio.run(self.server_start_main())

    def send(self, data: bytes | str):
        from websockets.sync.client import connect

        logger.info(f"Sending data to {self.host_url}")
        with connect(self.host_url) as websocket:
            websocket.send(data)

    def is_target_alive(self) -> bool:
        """sends a websocket message to the target and wait for a response"""
        from websockets.sync.client import connect

        logger.info(f"Checking if target is alive at {self.host_url}")
        with connect(self.host_url) as websocket:
            websocket.send("ping")
            try:
                result = websocket.recv(timeout=1)
            except TimeoutError:
                logger.info("Target did not respond")
                return False
            if result == "pong":
                logger.info("Target is alive")
                return True

        return False

    def send_scene(
        self,
        scene: trimesh.Scene,
        animation_store=None,
        auto_reposition=True,
        scene_action: SceneAction = SceneAction.NEW,
        scene_action_arg: str = None,
        save_to_file_path: str | None = None,
        **kwargs,
    ):

        translation_list = None
        if auto_reposition:
            y_delta = -scene.bounding_box.bounds[0][1]
            c = -scene.bounding_box.centroid
            translation = np.asarray([c[0], y_delta, c[2]])
            # move the scene to the origin
            logger.info(f"Applying translation {translation}")
            scene.apply_translation(translation)
            translation_list = translation.astype(float).tolist()

        with io.BytesIO() as data:
            scene.export(file_obj=data, file_type="glb", buffer_postprocessor=animation_store)

            msg = WsRenderMessage(
                data=base64.b64encode(data.getvalue()).decode(),
                look_at=kwargs.get("look_at", None),
                camera_position=kwargs.get("camera_position", None),
                model_translation=translation_list,
                scene_action=scene_action,
                scene_action_arg=scene_action_arg,
            )

            self.send(json.dumps(msg.__dict__))

            if save_to_file_path is not None:
                data.seek(0)
                logger.info(f"Saving scene to {save_to_file_path}")
                with open(save_to_file_path, "wb") as f:
                    f.write(data.getvalue())

    @property
    def host_url(self):
        if not self.host.startswith("ws://"):
            return f"ws://{self.host}:{self.port}"

        return f"{self.host}:{self.port}"


async def _check_server_running(host="ws://localhost", port=8765):
    try:
        async with websockets.connect(f"{host}:{port}"):
            logger.info(f"WebSocket server is already running on ws://localhost:{port}")
            return True
    except Exception as e:
        logger.debug(e)
        logger.info("WebSocket server is not running")
        return False


def is_server_running(host="localhost", port=8765):
    if host == "localhost":
        host = "ws://localhost"
    loop = asyncio.get_event_loop()
    if loop.is_running():
        from websockets.sync.client import connect as sync_connect

        try:
            with sync_connect(f"{host}:{port}"):
                logger.info(f"WebSocket server is already running on ws://localhost:{port}")
                return True
        except Exception as e:
            logger.debug(e)
            logger.info("WebSocket server is not running")
            return False
    else:
        # If the loop is not running, use run_until_complete
        return loop.run_until_complete(_check_server_running(host, port))


def start_server(shared_queue: Queue = None, host="localhost", port=8765):
    _server = WebSocketServer(host=host, port=port, message_queue=shared_queue)
    _server.start()


def start_server_in_thread(host, port, client_origins: list[str], debug_mode):
    origins = []
    if client_origins is None:
        origins.append("http://localhost:5173")  # development server
        for i in range(8888, 8899):  # local jupyter servers
            origins.append(f"http://localhost:{i}")
        origins.append("null")  # local html
    else:
        origins = client_origins

    _server = WebSocketServer(host=host, port=port, client_origins=origins, debug_mode=debug_mode)

    # Create a new thread that runs the server's start method
    server_thread = threading.Thread(target=_server.start)

    # Start the thread
    server_thread.start()


def start_external_ws_server(server_exe, server_args):
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


@dataclass
class WsRenderMessage:
    data: str  # This will hold the Base64 encoded bytes
    look_at: list[float, float, float] | None = None
    camera_position: list[float, float, float] | None = None
    model_translation: list[float, float, float] | None = None
    scene_action: SceneAction = SceneAction.REPLACE
    scene_action_arg: str = None


def send_to_viewer(
    part: ada.Part | trimesh.Scene, host="localhost", port=8765, origins: list[str] = None, meta: dict = None
):
    if origins is None:
        send_to_local_viewer(part, host=host, port=port)
    else:
        send_to_web_viewer(part, port=port, origins=origins, meta=meta)


def start_ws_server(
    host="localhost",
    port=8765,
    server_exe: pathlib.Path = None,
    server_args: list[str] = None,
    run_in_thread=False,
    origins: list[str] = None,
    debug_mode=False,
    override_binder_check=False,
) -> WebSocketServer:
    ws = WebSocketServer(host=host, port=port)

    # Check if we are running in a binder environment
    res = os.getenv("BINDER_SERVICE_HOST", None)
    if res is not None and override_binder_check is False:
        logger.info(
            "Running in binder environment, starting server in thread. Pass override_binder_check=True to override"
        )
        logger.warning("Binder does not support websockets, so you will not be able to send data to the viewer")
        run_in_thread = True

    if ws.check_server_running() is False:
        if run_in_thread:
            start_server_in_thread(host=host, port=port, client_origins=origins, debug_mode=debug_mode)
        else:
            start_external_ws_server(server_exe, server_args)

        while ws.check_server_running() is False:
            time.sleep(0.1)

    return ws


def send_to_ws_server(
    data: str | bytes, host="localhost", port=8765, server_exe: pathlib.Path = None, server_args: list[str] = None
):
    ws = start_ws_server(host=host, port=port, server_exe=server_exe, server_args=server_args)

    ws.send(data)


def send_to_local_viewer(part: ada.Part | trimesh.Scene, host="localhost", port=8765):
    from ada.visit.rendering.render_pygfx import PYGFX_RENDERER_EXE_PY

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
