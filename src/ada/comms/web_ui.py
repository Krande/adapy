import functools
import http.server
import os
import socketserver
import threading
import webbrowser

from ada.visit.rendering.renderer_react import RendererReact


# Define the custom request handler
class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(
        self, *args, unique_id, ws_port, node_editor_only=False, target_instance=None, directory=None, **kwargs
    ):
        self.unique_id = unique_id
        self.ws_port = ws_port  # Use the actual WebSocket port
        self.node_editor_only = node_editor_only
        self.target_instance = target_instance
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            # Serve the index.html file with replacements
            index_file_path = os.path.join(self.directory, "index.html")
            try:
                with open(index_file_path, "r", encoding="utf-8") as f:
                    html_content = f.read()

                replacement_str = ""
                if self.unique_id is not None:
                    replacement_str += f'<script>window.WEBSOCKET_ID = "{self.unique_id}";</script>'
                if self.ws_port is not None:
                    replacement_str += f"\n<script>window.WEBSOCKET_PORT = {self.ws_port};</script>"
                if self.node_editor_only:
                    replacement_str += "\n<script>window.NODE_EDITOR_ONLY = true;</script>"
                if self.target_instance is not None:
                    replacement_str += f'\n<script>window.TARGET_INSTANCE_ID = "{self.target_instance}";</script>'

                # Perform the replacements
                modified_html_content = html_content.replace("<!--STARTUP_CONFIG_PLACEHOLDER-->", replacement_str)

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


def start_serving(
    web_port=5173,
    ws_port=8765,
    unique_id=None,
    target_instance=None,
    node_editor_only=False,
    non_blocking=False,
    auto_open=False,
) -> tuple[socketserver.ThreadingTCPServer, threading.Thread] | None:
    rr = RendererReact()
    web_dir = rr.local_html_path.parent
    # Create a partial function to pass the directory to the handler
    handler = functools.partial(
        CustomHTTPRequestHandler,
        ws_port=ws_port,
        unique_id=unique_id,
        node_editor_only=node_editor_only,
        target_instance=target_instance,
        directory=str(web_dir),
    )

    class ThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    # Use port 0 to have the OS assign an available port
    server = ThreadingTCPServer(("localhost", web_port), handler)
    port = server.server_address[1]
    print(
        f"Web UI server started on port {port} with WebSocket port {ws_port} and unique ID {unique_id} and target instance {target_instance}"
    )

    # Open the default web browser
    if auto_open:
        webbrowser.open(f"http://localhost:{port}")

    def start_server():
        server.serve_forever()

    if non_blocking:
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        return server, server_thread
    else:
        start_server()
