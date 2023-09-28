import argparse
import asyncio
from dataclasses import dataclass, field
from multiprocessing import Queue

import websockets

from ada.config import logger


@dataclass
class WebSocketServer:
    client_origins: list[str] = field(default_factory=list)
    clients: set = field(default_factory=set)
    port: int = 8765
    host: str = "localhost"
    message_queue: Queue = None

    def check_server_running(self):
        host = self.host
        if host == "localhost":
            host = "ws://localhost"
        if is_server_running(host, self.port):
            return True
        else:
            return False

    async def handler(self, websocket):
        if websocket.origin in self.client_origins:
            self.clients.add(websocket)

        async for message in websocket:
            if self.message_queue is not None:
                self.message_queue.put(message)
            await self.update_clients(message)

    async def update_clients(self, data):
        """This will update all clients with the latest data"""
        for client in self.clients:
            if client.open and client in self.clients:
                await client.send(data)

    async def server_start_main(self):
        async with websockets.serve(self.handler, self.host, self.port, max_size=10**9):
            await asyncio.Future()  # run forever

    def start(self):
        logger.setLevel("INFO")
        logger.info(f"Starting server {self.host}:{self.port}")
        asyncio.run(self.server_start_main())

    def send(self, data: bytes):
        from websockets.sync.client import connect

        logger.info(f"Sending data to {self.host_url}")
        with connect(self.host_url) as websocket:
            websocket.send(data)

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
    return asyncio.run(_check_server_running(host, port))


def start_server(shared_queue: Queue = None, host="localhost", port=8765):
    _server = WebSocketServer(host=host, port=port, message_queue=shared_queue)
    _server.start()


if __name__ == "__main__":
    argparse = argparse.ArgumentParser()
    argparse.add_argument("--port", type=int, default=8765)
    argparse.add_argument("--origins", type=str)
    argparse.add_argument("--host", type=str, default="localhost")
    args = argparse.parse_args()

    server = WebSocketServer(host=args.host, port=args.port, client_origins=args.origins.split(";"))
    server.start()
