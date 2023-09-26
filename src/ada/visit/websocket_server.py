import argparse
import asyncio
from dataclasses import dataclass, field
from multiprocessing import Queue

import websockets

from ada.config import logger

message_queue = Queue()


async def receive_messages(websocket):
    async for message in websocket:
        await consumer(message)


@dataclass
class WebSocketServer:
    client_origins: list[str] = field(default_factory=list)
    clients: set = field(default_factory=set)
    port: int = 8765
    host: str = "localhost"
    message_queue: Queue = field(default_factory=Queue)

    def check_server_running(self):
        host = self.host
        if host == 'localhost':
            host = 'ws://localhost'
        if is_server_running(host, self.port):
            return True
        else:
            return False

    async def handler(self, websocket):
        if websocket.origin in self.client_origins:
            self.clients.add(websocket)

        async for message in websocket:
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


async def consumer(data):
    message_queue.put(data)


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


async def server_start_main(port):
    async with websockets.serve(receive_messages, "localhost", port, max_size=10**9):
        await asyncio.Future()  # run forever


def start_server(shared_queue: Queue = None, host="localhost", port=8765):
    if shared_queue is not None:
        global message_queue
        message_queue = shared_queue

    # asyncio.run(server_start_main(port))
    _server = WebSocketServer(host=host, port=port)
    _server.start()


if __name__ == "__main__":
    argparse = argparse.ArgumentParser()
    argparse.add_argument("--port", type=int, default=8765)
    argparse.add_argument("--origins", type=str)
    argparse.add_argument("--host", type=str, default="localhost")
    args = argparse.parse_args()

    server = WebSocketServer(host=args.host, port=args.port, client_origins=args.origins.split(";"))
    server.start()
