import asyncio

from ada.comms.wsock.server import WebSocketAsyncServer
from ada.config import logger


async def start_async_server():
    server = WebSocketAsyncServer("localhost", 8765, debug=True)
    await server.start_async()


if __name__ == "__main__":
    logger.setLevel("DEBUG")
    asyncio.run(start_async_server())
