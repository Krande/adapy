import asyncio
from dataclasses import dataclass

import pytest

from ada.comms.fb_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.fb_serializer import serialize_message
from ada.comms.wsock_client_async import WebSocketClientAsync
from ada.comms.wsock_server import WebSocketAsyncServer, handle_partial_message
from ada.config import logger

WS_HOST = "localhost"
WS_PORT = 1325


async def start():
    ws_server = WebSocketAsyncServer(WS_HOST, WS_PORT)

    try:
        ws = await ws_server.start_async()
        print(f"WebSocket server started on ws://{WS_HOST}:{WS_PORT}")
        await ws.wait_closed()
    except asyncio.CancelledError:
        await ws_server.stop()  # Properly stop the server
        raise  # Reraise to ensure proper handling in fixtures


@pytest.fixture(scope="session")
def event_loop():
    return asyncio.get_event_loop()


@pytest.fixture(autouse=True, scope="session")
def ws_server(event_loop):
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


@pytest.fixture(scope="session")
def mock_async_web_client(event_loop) -> MockWebParams:
    task = asyncio.ensure_future(start_mock_web_client_connection(WS_HOST, WS_PORT), loop=event_loop)

    yield MockWebParams(WS_HOST, WS_PORT, "web")  # Use 'yield' to wait for the fixture to complete

    # Cancel the task and wait for it to finish
    task.cancel()
    event_loop.run_until_complete(task)
