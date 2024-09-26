import asyncio
import threading
from dataclasses import dataclass

import pytest
import pytest_asyncio

from ada.comms.fb_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.fb_serializer import serialize_message
from ada.comms.wsock_client_async import WebSocketClientAsync
from ada.comms.wsock_server import WebSocketAsyncServer, handle_partial_message
from ada.config import logger

WS_HOST = "localhost"
WS_PORT = 1325


@pytest.fixture(scope="session")
def ws_server():
    # Function to run the WebSocket server in a separate thread
    def start_ws_server(loop):
        asyncio.set_event_loop(loop)
        ws_server_instance = WebSocketAsyncServer(WS_HOST, WS_PORT)
        loop.run_until_complete(ws_server_instance.start_async())
        print(f"WebSocket server started on ws://{WS_HOST}:{WS_PORT}")
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(ws_server_instance.stop())
            print("WebSocket server stopped")

    # Create a new event loop for the WebSocket server
    ws_loop = asyncio.new_event_loop()
    ws_thread = threading.Thread(target=start_ws_server, args=(ws_loop,), daemon=True)
    ws_thread.start()

    # Wait a moment to ensure the server has started
    # time.sleep(1)

    try:
        yield WS_HOST, WS_PORT
    finally:
        # Stop the WebSocket server
        ws_loop.call_soon_threadsafe(ws_loop.stop)
        ws_thread.join()


async def reply_ping(msg: MessageDC, ws_client: WebSocketClientAsync):
    message = MessageDC(
        instance_id=ws_client.instance_id,
        command_type=CommandTypeDC.PONG,
        target_id=msg.instance_id,
        target_group=TargetTypeDC.LOCAL,
        client_type=TargetTypeDC.WEB,
    )

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_message(message)
    await ws_client.websocket.send(flatbuffer_data)


# Additional instance to connect to the WebSocket server
async def start_mock_web_client_connection(host, port):
    uri = f"ws://{host}:{port}"

    async with WebSocketClientAsync(host, port, "web") as ws_client:
        websocket = ws_client.websocket
        logger.debug(f"Connected to server: {uri}")
        try:
            async for message in websocket:
                logger.debug(f"Received message from server: {message}")
                msg = await handle_partial_message(message)

                if msg.target_group != ws_client.client_type:
                    continue

                if msg.command_type == CommandTypeDC.PING:
                    await reply_ping(msg, ws_client)

        except asyncio.CancelledError as e:
            logger.debug("Connection to server was cancelled due to: ", e)


@dataclass
class MockWebParams:
    host: str
    port: int
    client_type: TargetTypeDC.WEB


@pytest_asyncio.fixture
def mock_async_web_client(event_loop) -> MockWebParams:
    task = asyncio.ensure_future(start_mock_web_client_connection(WS_HOST, WS_PORT), loop=event_loop)

    yield MockWebParams(WS_HOST, WS_PORT, "web")  # Use 'yield' to wait for the fixture to complete

    # Cancel the task and wait for it to finish
    task.cancel()
    event_loop.run_until_complete(task)
