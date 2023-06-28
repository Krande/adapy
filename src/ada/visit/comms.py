from multiprocessing import Queue

import asyncio
import websockets

from ada.config import logger

message_queue = Queue()


async def receive_messages(websocket):
    async for message in websocket:
        await consumer(message)


async def consumer(data):
    message_queue.put(data)


async def server_start_main():
    async with websockets.serve(receive_messages, "localhost", 8765):
        await asyncio.Future()  # run forever


def start_server(shared_queue: Queue=None):
    if shared_queue is not None:
        global message_queue
        message_queue = shared_queue

    logger.info("Starting server")
    asyncio.run(server_start_main())


if __name__ == "__main__":
    logger.setLevel("INFO")
    start_server()
