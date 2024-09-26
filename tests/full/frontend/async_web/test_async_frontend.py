import asyncio
import functools
import http.server
import socketserver
import threading

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from ada.comms.wsock_client_async import WebSocketClientAsync
from ada.comms.wsock_server import WebSocketAsyncServer
from ada.visit.rendering.renderer_react import RendererReact

WS_HOST = "localhost"
WS_PORT = 8765


# No custom event_loop fixture; rely on pytest-asyncio


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
        yield
    finally:
        # Stop the WebSocket server
        ws_loop.call_soon_threadsafe(ws_loop.stop)
        ws_thread.join()


@pytest_asyncio.fixture(scope="module")
async def http_server():
    # if port
    rr = RendererReact()
    web_dir = rr.local_html_path.parent

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web_dir))

    class ThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    server = ThreadingTCPServer(("localhost", 7711), handler)
    port = server.server_address[1]

    def start_server():
        server.serve_forever()

    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()

    # await asyncio.sleep(1)  # Ensure the server is ready

    try:
        yield port  # Provide the port to the test
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


@pytest.mark.asyncio
async def test_ws_connection(ws_server):
    # Ensure WebSocket is connecting to the correct port
    async with WebSocketClientAsync(WS_HOST, WS_PORT, "local") as ws_client:
        await ws_client.check_target_liveness()


@pytest.mark.asyncio
async def test_basic_frontend(http_server):
    port = http_server
    host = "localhost"
    url = f"http://{host}:{port}/index.html"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        browser_page = await browser.new_page()

        response = await browser_page.goto(url, timeout=10000)
        assert response.status == 200
        title = await browser_page.title()
        assert title == "ADA-PY Viewer"  # Replace with your expected title

        await browser.close()


@pytest.mark.asyncio
async def test_frontend_ws_client_connection(http_server, ws_server):
    port = http_server
    host = "localhost"
    url = f"http://{host}:{port}/index.html"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        browser_page = await browser.new_page()

        response = await browser_page.goto(url, timeout=10000)
        assert response.status == 200

        async with WebSocketClientAsync(WS_HOST, WS_PORT, "local") as ws_client:
            await ws_client.check_target_liveness()

        await browser.close()
