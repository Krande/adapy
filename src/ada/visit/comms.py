import asyncio
import io
import pathlib
import subprocess
import sys
import time
from multiprocessing import Queue

import websockets

import ada
from ada.config import logger

message_queue = Queue()
RENDERER_EXE_PY = pathlib.Path(__file__).parent / "render_pygfx.py"


async def receive_messages(websocket):
    async for message in websocket:
        await consumer(message)


async def consumer(data):
    message_queue.put(data)


async def _check_server_running():
    try:
        async with websockets.connect("ws://localhost:8765"):
            logger.info("WebSocket server is already running on ws://localhost:8765")
            return True
    except Exception as e:
        logger.debug(e)
        logger.info("WebSocket server is not running")
        return False


def is_server_running():
    return asyncio.run(_check_server_running())


async def server_start_main():
    async with websockets.serve(receive_messages, "localhost", 8765, max_size=10 ** 9):
        await asyncio.Future()  # run forever


def start_server(shared_queue: Queue = None):
    if shared_queue is not None:
        global message_queue
        message_queue = shared_queue

    logger.info("Starting server")
    asyncio.run(server_start_main())


def send_to_viewer(part: ada.Part):
    """Send a part to the viewer. This will start the viewer if it is not already running."""
    from websockets.sync.client import connect

    if is_server_running() is False:
        logger.info("Starting server in separate process")
        # Start the server in a separate process that opens a new shell window (on Windows)
        subprocess.Popen(['start', 'cmd.exe', '/K', sys.executable, str(RENDERER_EXE_PY)], shell=True)
        while is_server_running() is False:
            time.sleep(0.1)

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
