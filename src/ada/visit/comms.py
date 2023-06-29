import io
import time
from multiprocessing import Queue

import asyncio
import websockets

import ada
from ada.config import logger

message_queue = Queue()


async def receive_messages(websocket):
    async for message in websocket:
        await consumer(message)


async def consumer(data):
    message_queue.put(data)


async def server_start_main():
    async with websockets.serve(receive_messages, "localhost", 8765, max_size=10**9):
        await asyncio.Future()  # run forever


def start_server(shared_queue: Queue = None):
    if shared_queue is not None:
        global message_queue
        message_queue = shared_queue

    logger.info("Starting server")
    asyncio.run(server_start_main())


def send_to_viewer(part: ada.Part):
    from websockets.sync.client import connect
    start = time.time()
    data = io.BytesIO()
    part.to_trimesh_scene().export(data, file_type="glb")
    end = time.time()
    logger.info(f"Exported to glb in {end - start:.2f} seconds")
    with connect("ws://localhost:8765") as websocket:
        websocket.send(data.getvalue())


if __name__ == "__main__":
    logger.setLevel("INFO")
    start_server()
