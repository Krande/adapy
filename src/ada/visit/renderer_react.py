import os

import zipfile

import pathlib

from ada.visit.colors import Color
from ada.visit.render_backend import (
    SqLiteBackend,
)
from ada.visit.utils import in_notebook

BG_GRAY = Color(57, 57, 57)
PICKED_COLOR = Color(0, 123, 255)
THIS_DIR = pathlib.Path(__file__).parent.absolute()
ZIP_VIEWER = THIS_DIR / "resources" / "index.zip"


class RendererReact:
    def __init__(self, render_backend=SqLiteBackend(), local_html_path=THIS_DIR / "resources" / "index.html"):
        self.backend = render_backend
        self.local_html_path = local_html_path

        if not local_html_path.exists():
            archive = zipfile.ZipFile(ZIP_VIEWER)
            archive.extractall(THIS_DIR / "resources")

    def show(self):
        if in_notebook():
            return self._render_in_notebook()
        else:
            # open html file in browser
            os.startfile(self.local_html_path)

    def _render_in_notebook(self):
        from IPython.display import IFrame

        return IFrame(src=self.local_html_path, width="100%", height=500)


def main():
    RendererReact().show()


if __name__ == "__main__":
    main()
