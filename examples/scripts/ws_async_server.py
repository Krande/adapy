import asyncio

from ada.comms.wsock_server import WebSocketAsyncServer
from ada.config import logger


async def start_async_server():
    server = WebSocketAsyncServer("localhost", 8765)
    await server.start_async()


if __name__ == "__main__":
    logger.setLevel("DEBUG")
    # start_ws_async_server()
    asyncio.run(start_async_server())
