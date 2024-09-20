import asyncio

import flatbuffers
import pytest

from ada.comms.fb_model_gen import CommandTypeDC, MessageDC
from ada.comms.fb_serializer import serialize_message
from ada.comms.wsockets import (
    WebSocketClientAsync,
    WebSocketAsyncServer,
    handle_partial_message
)
from ada.config import logger

HOST = "localhost"
PORT = 1325


@pytest.fixture
def mock_host():
    """Fixture to provide the mock host."""
    return HOST


@pytest.fixture
def mock_port():
    """Fixture to provide the mock port."""
    return PORT


async def start():
    loop = asyncio.get_event_loop()
    ws_server = WebSocketAsyncServer(HOST, PORT)

    try:
        ws = await ws_server.start_async()
        print(f"WebSocket server started on ws://{HOST}:{PORT}")
        await ws.wait_closed()
    except asyncio.CancelledError:
        await ws_server.stop()  # Properly stop the server
        raise  # Reraise to ensure proper handling in fixtures


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
        try:
            event_loop.run_until_complete(task)  # Wait for task to finish
        except asyncio.CancelledError:
            pass  # Suppress the CancelledError to prevent it from propagating


# Additional instance to connect to the WebSocket server
async def start_mock_web_client_connection():
    uri = f"ws://{HOST}:{PORT}"

    async with WebSocketClientAsync(HOST, PORT, "web") as ws_client:
        websocket = ws_client.websocket
        logger.debug(f"Connected to server: {uri}")
        try:
            async for message in websocket:
                logger.debug(f"Received message from server: {message}")
                msg = await handle_partial_message(message)

                if msg.target_group != ws_client.client_type:
                    continue
                if msg.command_type == CommandTypeDC.PING:
                    message = MessageDC(
                        instance_id=ws_client.instance_id,
                        command_type=CommandTypeDC.PONG,
                        target_id=msg.instance_id,
                        target_group="local",
                        client_type="web",
                    )

                    # Initialize the FlatBuffer builder
                    builder = flatbuffers.Builder(1024)

                    # Serialize the dataclass message into a FlatBuffer
                    flatbuffer_data = serialize_message(builder, message)
                    await ws_client.websocket.send(flatbuffer_data)
        except asyncio.CancelledError as e:
            logger.debug("Connection to server was cancelled due to: ", e)


@pytest.fixture(scope="session")
def web_client(event_loop):
    task = asyncio.ensure_future(start_mock_web_client_connection(), loop=event_loop)

    yield  # Use 'yield' to wait for the fixture to complete

    # Cancel the task and wait for it to finish
    task.cancel()
    event_loop.run_until_complete(task)
