import argparse
import asyncio

from ada.comms.wsock_server import WebSocketAsyncServer
from ada.config import logger
from ada.visit.rendering.renderer_react import RendererReact


async def start_async_server():
    server = WebSocketAsyncServer("localhost", 8765)
    await server.start_async()


def ws_cli_app():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dont-open-viewer", action="store_true")
    args = parser.parse_args()

    if not args.dont_open_viewer:
        RendererReact().show()

    if args.debug:
        logger.setLevel("DEBUG")

    asyncio.run(start_async_server())


if __name__ == "__main__":
    ws_cli_app()
