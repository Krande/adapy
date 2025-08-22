import asyncio
import contextlib
import http.server
import os
import threading
from dataclasses import dataclass

import pytest
import pytest_asyncio

from ada.comms.fb_wrap_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.web_ui import start_serving
from ada.comms.wsock_client_async import WebSocketClientAsync
from ada.comms.wsock_server import WebSocketAsyncServer, handle_partial_message
from ada.config import logger

WS_HOST = "localhost"
WS_PORT = 1122


@dataclass
class MockHttpServer:
    host: str
    port: int
    _url_str: str = None

    @property
    def url(self):
        if self._url_str:
            return self._url_str
        return f"http://{self.host}:{self.port}"


@dataclass
class MockWSClient:
    host: str
    port: int

    @property
    def url(self):
        return f"ws://{self.host}:{self.port}"


@pytest.fixture(scope="session")
def ws_server() -> MockWSClient:
    def start_ws_server(loop):
        asyncio.set_event_loop(loop)
        ws_server_instance = WebSocketAsyncServer(WS_HOST, WS_PORT)
        loop.run_until_complete(ws_server_instance.start_async())
        print(f"WebSocket server started on ws://{WS_HOST}:{WS_PORT}")
        try:
            loop.run_forever()
        finally:
            print("WebSocket server stopped")

    ws_loop = asyncio.new_event_loop()
    ws_thread = threading.Thread(target=start_ws_server, args=(ws_loop,), daemon=True)
    ws_thread.start()

    try:
        yield MockWSClient(WS_HOST, WS_PORT)
    finally:
        # Safely attempt to stop the loop
        def teardown():
            try:
                pending_tasks = asyncio.all_tasks(loop=ws_loop)
                for task in pending_tasks:
                    task.cancel()
                ws_loop.call_soon_threadsafe(ws_loop.stop)
            except Exception as e:
                print(f"Error during ws_server teardown: {e}")

        # Wrap teardown in a timeout
        done_event = threading.Event()

        def teardown_wrapper():
            teardown()
            ws_thread.join(timeout=5)
            done_event.set()

        watchdog = threading.Thread(target=teardown_wrapper)
        watchdog.start()
        watchdog.join(timeout=6)

        if not done_event.is_set():
            print("⚠️ Timeout in ws_server fixture teardown. WebSocket server thread didn't shut down in time.")


# Define the custom request handler
class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, unique_id, ws_port, directory=None, **kwargs):
        self.unique_id = unique_id
        self.ws_port = ws_port  # Use the actual WebSocket port
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            # Serve the index.html file with replacements
            index_file_path = os.path.join(self.directory, "index.html")
            try:
                with open(index_file_path, "r", encoding="utf-8") as f:
                    html_content = f.read()

                # Perform the replacements
                modified_html_content = html_content.replace(
                    "<!--STARTUP_CONFIG_PLACEHOLDER-->",
                    f'<script>window.WEBSOCKET_ID = "{self.unique_id}";</script>\n'
                    f"<script>window.WEBSOCKET_PORT = {self.ws_port};</script>",
                )

                # Send response
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(modified_html_content.encode("utf-8"))
            except Exception as e:
                self.send_error(500, f"Internal Server Error: {e}")
        else:
            # For other files, use the default handler
            super().do_GET()


@pytest.fixture(scope="module")
def http_server() -> MockHttpServer:
    # Generate a unique ID or obtain it from your application logic
    unique_id: int = 88442233

    server, server_thread = start_serving(
        web_port=0, ws_port=WS_PORT, unique_id=unique_id, node_editor_only=False, non_blocking=True
    )
    port = server.server_address[1]
    try:
        yield MockHttpServer("localhost", port)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


async def reply_ping(msg: MessageDC, ws_client: WebSocketClientAsync):
    message = MessageDC(
        instance_id=ws_client.instance_id,
        command_type=CommandTypeDC.PONG,
        target_id=msg.instance_id,
        target_group=TargetTypeDC.LOCAL,
        client_type=TargetTypeDC.WEB,
    )

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_root_message(message)
    await ws_client.websocket.send(flatbuffer_data)


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
            logger.debug("Connection to server was cancelled", exc_info=e)


@dataclass
class MockWebParams:
    host: str
    port: int
    client_type: TargetTypeDC.WEB


@pytest_asyncio.fixture(scope="module")
async def mock_async_web_client() -> MockWebParams:
    # Schedule on the running loop managed by pytest-asyncio
    task = asyncio.create_task(start_mock_web_client_connection(WS_HOST, WS_PORT))

    try:
        yield MockWebParams(WS_HOST, WS_PORT, TargetTypeDC.WEB)
    finally:
        task.cancel()
        # Await the task but suppress the CancelledError expected from a cancelled task
        with contextlib.suppress(asyncio.CancelledError):
            await task
