import asyncio
import subprocess

import pytest
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

import ada
from ada.comms.web_ui import start_serving
from ada.comms.wsock_client_async import WebSocketClientAsync


@pytest.fixture(scope="session", autouse=True)
def ensure_playwright_installed():
    with sync_playwright() as playwright:
        try:
            # Try getting the executable path of Chromium
            _ = playwright.chromium.launch(headless=True)
        except Exception as e:
            print(f"Playwright browsers are missing [{e}]. Installing them now...")
            subprocess.run(["playwright", "install"], check=True)


@pytest.mark.timeout(10)  # seconds
@pytest.mark.asyncio
async def test_ws_server_no_client(ws_server):
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        assert await ws_client.check_target_liveness() is False


@pytest.mark.timeout(10)  # seconds
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


@pytest.mark.timeout(10)  # seconds
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


@pytest.mark.timeout(10)  # seconds
@pytest.mark.asyncio
async def test_front_multiple_connections(ws_server):
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    web_ports = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)

        # Viewer 1
        unique_id = 1
        server, server_thread = start_serving(
            web_port=0, ws_port=ws_server.port, unique_id=unique_id, node_editor_only=False, non_blocking=True
        )
        port = server.server_address[1]
        web_ports.append(port)
        browser_page = await browser.new_page()
        response = await browser_page.goto(f"http://localhost:{port}", timeout=10000)
        assert response.status == 200

        async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
            # Wait until the viewer has actually connected
            for _ in range(30):  # wait up to 3s
                if await ws_client.check_target_liveness(target_id=unique_id):
                    break
                await asyncio.sleep(0.1)
            else:
                assert False, f"Viewer with ID {unique_id} failed to become live in time"
            # assert await ws_client.check_target_liveness(target_id=unique_id) is True

        bm.show(ws_port=ws_server.port, unique_viewer_id=unique_id, liveness_timeout=3)

        # Viewer 2
        unique_id = 2
        server, server_thread = start_serving(
            web_port=0, ws_port=ws_server.port, unique_id=unique_id, node_editor_only=False, non_blocking=True
        )
        port = server.server_address[1]
        web_ports.append(port)
        browser_page = await browser.new_page()
        response = await browser_page.goto(f"http://localhost:{port}", timeout=10000)
        assert response.status == 200

        async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
            assert await ws_client.check_target_liveness(target_id=unique_id) is True
        bm.show(ws_port=ws_server.port, unique_viewer_id=unique_id, liveness_timeout=3)
        #
        # # Viewer 3
        # unique_id = 3
        # server, server_thread = start_serving(
        #     web_port=0, ws_port=ws_server.port, unique_id=unique_id, node_editor_only=False, non_blocking=True
        # )
        # port = server.server_address[1]
        # web_ports.append(port)
        # browser_page = await browser.new_page()
        # response = await browser_page.goto(f"http://localhost:{port}", timeout=10000)
        # assert response.status == 200
        #
        # async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        #     assert await ws_client.check_target_liveness(target_id=unique_id) is True
        # bm.show(ws_port=ws_server.port, unique_viewer_id=unique_id, liveness_timeout=3)
        #
        # # Viewer 4
        # unique_id = 4
        # server, server_thread = start_serving(
        #     web_port=0, ws_port=ws_server.port, unique_id=unique_id, node_editor_only=False, non_blocking=True
        # )
        # port = server.server_address[1]
        # web_ports.append(port)
        # browser_page = await browser.new_page()
        # response = await browser_page.goto(f"http://localhost:{port}", timeout=10000)
        # assert response.status == 200
        #
        # async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        #     assert await ws_client.check_target_liveness(target_id=unique_id) is True
        # bm.show(ws_port=ws_server.port, unique_viewer_id=unique_id, liveness_timeout=3)
        #
        # await browser.close()
