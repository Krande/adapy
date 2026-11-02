"""``show(render_backend=...)`` opens the single-file viewer with that draw backend selected."""

from __future__ import annotations

import webbrowser
import zipfile

import pytest

from ada.visit.rendering import renderer_react


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    (pkg / "resources").mkdir(parents=True)
    with zipfile.ZipFile(pkg / "resources" / "index.zip", "w") as z:
        z.writestr("index.html", "<html>single file</html>")
    monkeypatch.setattr(renderer_react, "THIS_DIR", pkg)
    monkeypatch.setattr(renderer_react, "ZIP_VIEWER", pkg / "resources" / "index.zip")
    monkeypatch.setattr(renderer_react, "in_notebook", lambda: False)
    monkeypatch.delenv("ADA_VIEWER_BUNDLE", raising=False)
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    r = renderer_react.RendererReact()
    r.opened = opened
    return r


def test_no_backend_opens_the_viewer_itself(renderer):
    renderer.show()
    assert renderer.opened == [renderer.local_html_path.resolve().as_uri()]


def test_webgpu_opens_a_redirect_to_the_viewer_with_the_adacpp_renderer(renderer):
    renderer.show(render_backend="webgpu")
    launcher = renderer.local_html_path.with_name("index.webgpu.html")
    assert renderer.opened == [launcher.resolve().as_uri()]
    assert 'url=index.html?renderer=adacpp"' in launcher.read_text()


def test_an_unknown_backend_is_an_error(renderer):
    with pytest.raises(ValueError, match="render_backend"):
        renderer.show(render_backend="vulkan")
