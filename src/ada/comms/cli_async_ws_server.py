import argparse
import asyncio
import pathlib

from ada.comms.wsock_server import WebSocketAsyncServer
from ada.config import logger

WS_ASYNC_SERVER_PY = pathlib.Path(__file__)


async def start_async_server(host="localhost", port=8765, run_in_thread=False, log_level="DEBUG"):
    logger.setLevel(log_level)
    is_debug = False
    if log_level == "DEBUG":
        is_debug = True
    server = WebSocketAsyncServer(host, port, debug=is_debug)
    if run_in_thread:
        await server.run_in_background()
    else:
        await server.start_async()


def start_async_ws_server(host="localhost", port=8765, run_in_thread=False):
    asyncio.run(start_async_server(host, port, run_in_thread))


def ws_async_cli_app():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--origins", type=str, default="localhost")
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--open-viewer", action="store_true")
    parser.add_argument("--log-level", type=str, default="DEBUG")
    args = parser.parse_args()

    origins_list = []
    for origin in args.origins.split(";"):
        if origin == "localhost":
            origins_list.append("http://localhost:5173")  # development server
            for i in range(8888, 8899):  # local jupyter servers
                origins_list.append(f"http://localhost:{i}")
            origins_list.append("null")  # local html
        else:
            origins_list.append(origin)

    if args.open_viewer:
        from ada.visit.rendering.renderer_react import RendererReact

        RendererReact().show()

    asyncio.run(start_async_server(args.host, args.port, log_level=args.log_level))


if __name__ == "__main__":
    ws_async_cli_app()
