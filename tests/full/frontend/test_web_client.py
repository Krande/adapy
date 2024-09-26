import pytest
from playwright.async_api import async_playwright

from ada.comms.wsock_client_async import WebSocketClientAsync


@pytest.mark.asyncio
async def test_ws_server_no_client(ws_server):
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        assert await ws_client.check_target_liveness() is False


@pytest.mark.asyncio
async def test_basic_frontend(http_server):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        browser_page = await browser.new_page()

        response = await browser_page.goto(http_server.url, timeout=1000)
        assert response.status == 200
        title = await browser_page.title()
        assert title == "ADA-PY Viewer"  # Replace with your expected title

        await browser.close()


@pytest.mark.asyncio
async def test_frontend_ws_client_connection(http_server, ws_server):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        browser_page = await browser.new_page()

        response = await browser_page.goto(http_server.url, timeout=10000)
        assert response.status == 200

        async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
            assert await ws_client.check_target_liveness() is True

        await browser.close()
