import argparse
import asyncio
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from multiprocessing import Queue

import trimesh
import websockets

from ada.config import logger


def pretty_print_ports(client_origins):
    split_origins = defaultdict(list)
    for item in client_origins:
        result = re.search(r"(?P<protocol>\w+)://(?P<host>\w+):(?P<port>\d+)", item)
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
    return "\n".join(result)


@dataclass
class WebSocketServer:
    client_origins: list[str] = field(default_factory=list)
    clients: dict = field(default_factory=dict)
    port: int = 8765
    host: str = "localhost"
    message_queue: Queue = field(default_factory=Queue)
    debug_mode: bool = False

    def check_server_running(self):
        host = self.host
        if host == "localhost":
            host = "ws://localhost"
        if is_server_running(host, self.port):
            return True
        else:
            return False

    async def handler(self, websocket):
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

        async for message in websocket:
            if self.message_queue is not None:
                self.message_queue.put(message)
            await self.update_clients(message, websocket.origin)

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

    def send_scene(self, scene: trimesh.Scene, animation_store=None, **kwargs):
        import base64
        import json

        from ada.visit.comms import WsRenderMessage

        with io.BytesIO() as data:
            scene.export(file_obj=data, file_type="glb", buffer_postprocessor=animation_store)

            msg = WsRenderMessage(
                data=base64.b64encode(data.getvalue()).decode(),
                look_at=kwargs.get("look_at", None),
                camera_position=kwargs.get("camera_position", None),
            )

            self.send(json.dumps(msg.__dict__))

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


if __name__ == "__main__":
    argparse = argparse.ArgumentParser()
    argparse.add_argument("--port", type=int, default=8765)
    argparse.add_argument("--origins", type=str, default="localhost")
    argparse.add_argument("--host", type=str, default="localhost")
    argparse.add_argument("--debug", action="store_true")
    args = argparse.parse_args()

    origins_list = []
    for origin in args.origins.split(";"):
        if origin == "localhost":
            origins_list.append("http://localhost:5173")  # development server
            for i in range(8888, 8899):  # local jupyter servers
                origins_list.append(f"http://localhost:{i}")
            origins_list.append("null")  # local html
        else:
            origins_list.append(origin)

    server = WebSocketServer(host=args.host, port=args.port, client_origins=origins_list, debug_mode=args.debug)
    server.start()
