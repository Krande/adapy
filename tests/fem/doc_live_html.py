import asyncio
import os
import subprocess
import threading
import time

import websockets
from build_verification_report import create_fea_report
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ada.config import logger

html_inject = """
<script>
document.addEventListener('DOMContentLoaded', (event) => {
    document.querySelectorAll('a').forEach(link => {
        const href = link.getAttribute('href');
        if (href.startsWith('http')) { // Check if it's an external link
            link.addEventListener('click', (e) => {
                e.preventDefault(); // Prevent the default link behavior
                if (confirm(`Do you want to go to ${href}?`)) {
                    window.open(href, '_blank'); // Open in new tab
                }
            });
        }
    });
});
</script>
<script>
    const socket = new WebSocket('ws://localhost:__WS_PORT__');

    socket.onerror = function(event) {
        console.error("WebSocket error observed:", event);
    };

    socket.onmessage = function(event) {
        location.reload();
    };
</script>
<script>
    window.onload = function() {
        var imgs = document.querySelectorAll('img');
        imgs.forEach(function(img) {
            img.addEventListener('click', function() {
                img.classList.toggle('large');
                img.classList.toggle('zoomed');
                if (img.classList.contains('zoomed')) {
                    document.addEventListener('click', function(event) {
                        if (event.target !== img) {
                            img.classList.remove('zoomed');
                        }
                    });
                }
            });
        });
    };
</script>
<style>
    .large {
        width: 100%;
        height: auto;
        max-height: 100vh;
    }
    .zoomed {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        z-index: 9999;
        object-fit: contain;
        background: rgba(0, 0, 0, 0.5);
    }
    img {
        cursor: pointer;
    }
</style>
"""

websocket_global = None  # Global variable for the WebSocket connection


def insert_html_inject(html_file_path, ws_port):
    # insert the html just below the <head> tag
    with open(html_file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if "<head>" in line:
            lines.insert(i + 1, html_inject.replace("__WS_PORT__", str(ws_port)))
            break
    with open(html_file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


async def send_reload_message():
    if websocket_global is not None:
        await websocket_global.send("reload")


async def handler(websocket, path):
    global websocket_global
    websocket_global = websocket
    try:
        await websocket.recv()
    except websockets.exceptions.ConnectionClosedOK:
        logger.info("WebSocket connection closed")


class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, min_time_between_updates, ws_port=8221):
        self.last_update_time = 0
        self.min_time_between_updates = min_time_between_updates
        self.ws_port = ws_port
        self.ongoing_update = False
        self.update_lock = threading.Lock()  # Add a lock

    def on_modified(self, event):
        with self.update_lock:  # Acquire the lock
            if self.ongoing_update:
                return
            self.ongoing_update = True
        current_time = time.time()
        if current_time - self.last_update_time > self.min_time_between_updates:
            try:
                create_fea_report(False, False, export_format="html")
            except subprocess.CalledProcessError:
                logger.error("Error while updating the report")
                self.last_update_time = current_time
                self.ongoing_update = False
                return
            insert_html_inject("temp/_dist/ADA-FEA-verification.html", self.ws_port)
            asyncio.run(send_reload_message())
            # insert the html_inject into the index.html file located at temp/_dist/crash_barrier.html
            self.last_update_time = current_time
            logger.info(f"File {event.src_path} has been updated")

        with self.update_lock:  # Acquire the lock
            self.ongoing_update = False

    def start_websocket_server(self):
        logger.info(f"Starting WebSocket server on port {self.ws_port} [process ID: {os.getpid()}]")

        async def server():
            async with websockets.serve(handler, "localhost", self.ws_port):
                await asyncio.Future()  # run forever

        asyncio.run(server())


if __name__ == "__main__":
    logger.setLevel("INFO")
    event_handler = FileChangeHandler(min_time_between_updates=10, ws_port=8221)
    observer = Observer()
    observer.schedule(event_handler, path="./report", recursive=True)
    observer.start()

    # Start the WebSocket server in a separate thread
    event_handler.start_websocket_server()
