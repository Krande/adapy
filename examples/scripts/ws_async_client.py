import asyncio
import pathlib

from ada.comms.wsock_client_async import WebSocketClientAsync

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


async def update_scene():
    async with WebSocketClientAsync("localhost", 8765, "local") as ws_client:
        procedures = await ws_client.list_procedures()
        print(procedures)


if __name__ == "__main__":
    asyncio.run(update_scene())
