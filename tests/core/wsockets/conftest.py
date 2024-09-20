import json
from dataclasses import dataclass

import pytest
import asyncio

import websockets

from ada.visit.websocket_server import WebSocketServer, WebSocketClientAsync

HOST = "localhost"
PORT = 1325

connected_clients = set()


@pytest.fixture
def mock_host():
    return HOST


@pytest.fixture
def mock_port():
    return PORT


@dataclass
class ConnectedClient:
    client: websockets.WebSocketServerProtocol
    instance_id: int | None

    def __hash__(self):
        return hash(self.client)


async def async_ws_server(websocket, path):
    # Register web_client
    cc = ConnectedClient(websocket, None)
    connected_clients.add(cc)
    print(f"Client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            print(f"Received message from client: {message}")
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                print("Invalid message received")
                continue

            # Forward message to all other connected clients
            for client in connected_clients:
                if client.client == websocket:
                    if client.instance_id is None:
                        client.instance_id = msg.get("instance_id")
                    continue
                if msg["target_id"] is not None and client.instance_id != msg["target_id"]:
                    continue
                await client.client.send(message)
    finally:
        # Unregister web_client
        connected_clients.remove(websocket)
        print(f"Client disconnected: {websocket.remote_address}")


async def start():
    loop = asyncio.get_event_loop()

    try:
        # await server start here.
        ws = await websockets.serve(async_ws_server, HOST, PORT)
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

    async with WebSocketClientAsync(HOST, PORT, "web") as ws_client:
        websocket = ws_client.websocket
        print(f"Connected to server: {uri}")
        try:
            async for message in websocket:
                print(f"Received message from server: {message}")
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    print(f"Invalid message received")
                    continue
                if msg["target_group"] != ws_client.client_type:
                    continue
                if msg["message"] == "ping":
                    await ws_client.send("pong", target_id=msg["instance_id"], target_group="client")
        except asyncio.CancelledError as e:
            print("Connection to server was cancelled due to: ", e)


@pytest.fixture(scope="session")
def web_client(event_loop):
    task = asyncio.ensure_future(connect_to_server(), loop=event_loop)
    yield
    task.cancel()
    event_loop.run_until_complete(task)
