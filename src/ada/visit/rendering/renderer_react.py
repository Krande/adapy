from __future__ import annotations

import os
import pathlib
import zipfile
from typing import TYPE_CHECKING

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

    def get_notebook_renderer(self, height=500) -> HTML:
        import html

        from IPython.display import HTML

        # Copied from https://github.com/mikedh/trimesh/blob/main/trimesh/viewer/notebook.py#L51-L88
        as_html = self.local_html_path.read_text(encoding="utf-8")
        # escape the quotes in the HTML
        srcdoc = html.escape(as_html)
        # srcdoc = as_html.replace('"', "&quot;")
        # embed this puppy as the srcdoc attr of an IFframe
        # I tried this a dozen ways and this is the only one that works
        # display.IFrame/display.Javascript really, really don't work
        # div is to avoid IPython's pointless hardcoded warning
        embedded = HTML(
            " ".join(
                [
                    '<div><iframe srcdoc="{srcdoc}"',
                    'width="100%" height="{height}px"',
                    'style="border:none;"></iframe></div>',
                ]
            ).format(srcdoc=srcdoc, height=height)
        )
        return embedded

    def get_notebook_renderer_widget(self, height=500, target_id=None):
        from ada.visit.rendering.renderer_widget import WebSocketRenderer

        as_html = self.local_html_path.read_text(encoding="utf-8")
        renderer = WebSocketRenderer(as_html, height=height, unique_id=target_id)
        return renderer.display()


def main():
    RendererReact().show()


if __name__ == "__main__":
    main()
