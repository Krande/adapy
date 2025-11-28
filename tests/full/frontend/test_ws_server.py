import time

import pytest
from playwright.async_api import async_playwright

from ada.comms.wsock.client_async import WebSocketClientAsync


@pytest.mark.asyncio
async def test_list_procedures(ws_server):
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        procedures = await ws_client.list_procedures()
        assert procedures is None


@pytest.mark.asyncio
async def test_connected_clients(http_server, ws_server):
    # Start a playwright browser (which will connect to the ws server).
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        connected_web_clients = await ws_client.list_connected_web_clients()
        assert len(connected_web_clients) == 0  # At least the browser client should be connected

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        browser_page = await browser.new_page()

        await browser_page.goto(http_server.url, timeout=1000)
        time.sleep(1)
        # Then use WS client to list the number of clients
        async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
            connected_web_clients = await ws_client.list_connected_web_clients()
            assert len(connected_web_clients) == 1  # At least the browser client should be connected
