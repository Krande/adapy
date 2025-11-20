import functools
import http.server
import socketserver
import threading
import webbrowser

from ada.visit.rendering.renderer_react import RendererReact


# Define the custom request handler
class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        unique_id,
        ws_port,
        node_editor_only=False,
        target_instance=None,
        directory=None,
        embed_trimesh_scene=None,
        embed_base64_glb: bytes = None,
        renderer_obj: RendererReact = None,
        force_ws=False,
        gltf_buffer_postprocessor=None,
        gltf_tree_postprocessor=None,
        **kwargs,
    ):
        self.unique_id = unique_id
        self.ws_port = ws_port  # Use the actual WebSocket port
        self.node_editor_only = node_editor_only
        self.target_instance = target_instance
        self.embed_trimesh_scene = embed_trimesh_scene
        self.embed_base64_glb = embed_base64_glb
        self.renderer_obj = renderer_obj
        self.force_ws = force_ws
        self.gltf_buffer_postprocessor = gltf_buffer_postprocessor
        self.gltf_tree_postprocessor = gltf_tree_postprocessor
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            # Serve the index.html file with replacements
            try:
                modified_html_content = self.renderer_obj.get_html_with_injected_data(
                    self.unique_id,
                    self.ws_port,
                    embed_trimesh_scene=self.embed_trimesh_scene,
                    embed_base64_glb=self.embed_base64_glb,
                    force_ws=self.force_ws,
                    node_editor_only=self.node_editor_only,
                    target_instance=self.target_instance,
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


def start_serving(
    web_port=5174,
    ws_port=8765,
    unique_id=None,
    target_instance=None,
    node_editor_only=False,
    non_blocking=False,
    auto_open=False,
    embed_trimesh_scene=None,
    embed_base64_glb: str = None,
    renderer_obj: RendererReact = None,
    force_ws=False,
    gltf_buffer_postprocessor=None,
    gltf_tree_postprocessor=None,
) -> tuple[socketserver.ThreadingTCPServer, threading.Thread] | None:
    rr = RendererReact()
    web_dir = rr.local_html_path.parent
    if renderer_obj is None:
        renderer_obj = rr
    # Create a partial function to pass the directory to the handler
    handler = functools.partial(
        CustomHTTPRequestHandler,
        ws_port=ws_port,
        unique_id=unique_id,
        node_editor_only=node_editor_only,
        target_instance=target_instance,
        directory=str(web_dir),
        embed_trimesh_scene=embed_trimesh_scene,
        embed_base64_glb=embed_base64_glb,
        renderer_obj=renderer_obj,
        force_ws=force_ws,
        gltf_buffer_postprocessor=gltf_buffer_postprocessor,
        gltf_tree_postprocessor=gltf_tree_postprocessor,
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
