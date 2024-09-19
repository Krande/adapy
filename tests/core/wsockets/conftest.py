import pytest
import asyncio

import websockets

from ada.visit.websocket_server import WebSocketServer

HOST = "localhost"
PORT = 1325

connected_clients = set()


async def echo(websocket, path):
    # Register web_client
    connected_clients.add(websocket)
    print(f"Client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            print(f"Received message from client: {message}")
            # Forward message to all other connected clients
            for client in connected_clients:
                if client != websocket:
                    await client.send(message)
    finally:
        # Unregister web_client
        connected_clients.remove(websocket)
        print(f"Client disconnected: {websocket.remote_address}")


async def start():
    loop = asyncio.get_event_loop()

    try:
        # await server start here.
        ws = await websockets.serve(echo, HOST, PORT)
        print(f"WebSocket server started on ws://{HOST}:{PORT}")
        await ws.wait_closed()

    except asyncio.CancelledError:
        ws.close()
    finally:
        loop.stop()


@pytest.fixture(scope="session")
def event_loop():
    return asyncio.get_event_loop()


@pytest.fixture(autouse=True, scope="session")
def server(event_loop):
    task = asyncio.ensure_future(start(), loop=event_loop)

    # Sleeps to allow the server boot-up.
    event_loop.run_until_complete(asyncio.sleep(1))

    try:
        yield
    finally:
        task.cancel()


# Additional instance to connect to the WebSocket server
async def connect_to_server():
    uri = f"ws://{HOST}:{PORT}"
    async with websockets.connect(uri) as websocket:
        print(f"Connected to server: {uri}")
        try:
            async for message in websocket:
                print(f"Received message from server: {message}")
                if message == "ping":
                    await websocket.send("pong")
        except asyncio.CancelledError:
            print("Connection to server was cancelled")


@pytest.fixture(scope="session")
def web_client(event_loop):
    task = asyncio.ensure_future(connect_to_server(), loop=event_loop)
    yield
    task.cancel()
    event_loop.run_until_complete(task)
