import functools
import http.server
import socketserver
import threading

import pytest
from playwright.sync_api import sync_playwright

from ada.visit.rendering.renderer_react import RendererReact


@pytest.fixture(scope="module")
def http_server():
    rr = RendererReact()
    web_dir = rr.local_html_path.parent

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web_dir))

    class ThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    server = ThreadingTCPServer(("localhost", 0), handler)
    port = server.server_address[1]

    def start_server():
        server.serve_forever()

    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()

    # time.sleep(1)  # Ensure the server is ready

    try:
        yield port  # Provide the port to the test
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


@pytest.fixture(scope="module")
def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def test_basic_frontend(http_server, browser_page):
    port = http_server
    url = f"http://localhost:{port}/index.html"
    response = browser_page.goto(url, timeout=10000)
    assert response.status == 200
    title = browser_page.title()
    assert title == "ADA-PY Viewer"  # Replace with your expected title
