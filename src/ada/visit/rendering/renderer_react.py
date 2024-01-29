import os
import pathlib
import zipfile

from ada.visit.colors import Color
from ada.visit.rendering.render_backend import SqLiteBackend
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
            return self.get_notebook_renderer()
        else:
            # open html file in browser
            os.startfile(self.local_html_path)

    def get_notebook_renderer(self, height=500):
        from IPython.display import HTML

        # Copied from https://github.com/mikedh/trimesh/blob/main/trimesh/viewer/notebook.py#L51-L88
        as_html = self.local_html_path.read_text(encoding="utf-8")
        # escape the quotes in the HTML
        srcdoc = as_html.replace('"', "&quot;")
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


def main():
    RendererReact().show()


if __name__ == "__main__":
    main()
