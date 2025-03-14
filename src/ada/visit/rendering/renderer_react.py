from __future__ import annotations

import os
import pathlib
import zipfile
from typing import TYPE_CHECKING

import trimesh

from ada.config import logger
from ada.visit.colors import Color
from ada.visit.rendering.render_backend import SqLiteBackend
from ada.visit.utils import in_notebook

if TYPE_CHECKING:
    from IPython.display import HTML

BG_GRAY = Color(57, 57, 57)
PICKED_COLOR = Color(0, 123, 255)
THIS_DIR = pathlib.Path(__file__).parent.absolute()
ZIP_VIEWER = THIS_DIR / "resources" / "index.zip"
HASH_FILE = ZIP_VIEWER.with_suffix(".hash")


class RendererReact:
    def __init__(self, render_backend=SqLiteBackend(), local_html_path=THIS_DIR / "resources" / "index.html"):
        self.backend = render_backend
        self.local_html_path = local_html_path

        self._extract_html()

    def _extract_html(self):
        from ada.core.utils import get_md5_hash_for_file

        hash_content = get_md5_hash_for_file(ZIP_VIEWER).hexdigest()
        if self.local_html_path.exists() and HASH_FILE.exists():
            with open(HASH_FILE, "r") as f:
                hash_stored = f.read()
            if hash_content == hash_stored:
                return

        logger.info("Extracting HTML viewer")
        archive = zipfile.ZipFile(ZIP_VIEWER)
        archive.extractall(THIS_DIR / "resources")

        # Update HASH file
        with open(HASH_FILE, "w") as f:
            f.write(hash_content)

    def show(self, target_id=None) -> None | HTML:
        if in_notebook():
            return self.get_notebook_renderer_widget(target_id=target_id)
        else:
            # open html file in browser
            os.startfile(self.local_html_path)

    def get_html_with_injected_data(
        self, target_id: int | None = None, ws_port: int | None = None, embed_trimesh_scene: trimesh.Scene | None = None
    ) -> str:
        import base64

        html_content = self.local_html_path.read_text(encoding="utf-8")

        html_inject_str = ""
        if target_id is not None:
            html_inject_str += f'<script>window.WEBSOCKET_ID = "{target_id}";</script>\n'
        if ws_port is not None:
            html_inject_str += f"<script>window.WEBSOCKET_PORT = {ws_port};</script>"

        if embed_trimesh_scene is not None:
            data = embed_trimesh_scene.export(file_type="glb")
            # encode as base64 string
            encoded = base64.b64encode(data).decode("utf-8")
            # replace keyword with our scene data
            html_inject_str += f'<script>window.B64GLTF = "{encoded}";</script>'

        # Inject the unique ID into the HTML content
        html_content = html_content.replace("<!--STARTUP_CONFIG_PLACEHOLDER-->", html_inject_str)

        return html_content

    @staticmethod
    def serve_html(
        web_port=5173, ws_port=8765, target_id: int | None = None, embed_trimesh_scene: trimesh.Scene | None = None
    ):
        from ada.comms.web_ui import start_serving

        return start_serving(
            web_port=web_port, ws_port=ws_port, unique_id=target_id, embed_trimesh_scene=embed_trimesh_scene
        )

    def get_notebook_renderer_widget(
        self,
        height: int = 500,
        target_id: int | None = None,
        ws_port: int | None = None,
        embed_trimesh_scene: trimesh.Scene | None = None,
    ) -> HTML:
        import html

        from IPython import display

        html_content = self.get_html_with_injected_data(target_id, ws_port, embed_trimesh_scene)

        # Escape and embed the HTML in the srcdoc of the iframe
        srcdoc = html.escape(html_content)
        # Create an IFrame widget wrapped in an HTML widget
        html_widget = display.HTML(
            f'<div><iframe srcdoc="{srcdoc}" width="100%" height="{height}px" style="border:none;"></iframe></div>'
        )

        return html_widget


def main():
    RendererReact().show()


if __name__ == "__main__":
    main()
