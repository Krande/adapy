"""An installed viewer bundle (adapy's viewer built with plugins) replaces the built-in viewer when asked for.

A package that ships plugins installs the viewer it built with them under share/ada/viewers/<name>/;
ADA_VIEWER_BUNDLE or --viewer picks it, for the REST app's static files and for show()."""

from __future__ import annotations

import json
import sys
import types
import zipfile

import pytest

import ada_cli.main
from ada.comms import viewer_bundles
from ada.comms.viewer_bundles import (
    ENV_BUNDLE,
    ENV_BUNDLE_PATH,
    list_viewer_bundles,
    resolve_viewer_bundle,
)


def _bundle(root, name="company", *, dist=True, embed=True):
    b = root / name
    b.mkdir(parents=True)
    (b / "bundle.json").write_text(
        json.dumps({"name": name, "title": "Company viewer", "adapy_version": "0.123.0", "plugins": ["capacity"]})
    )
    if dist:
        (b / "dist").mkdir()
        (b / "dist" / "index.html").write_text("<html>served</html>")
    if embed:
        with zipfile.ZipFile(b / "index.zip", "w") as z:
            z.writestr("index.html", "<html>single file</html>")
    return b


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """One bundle on ADA_VIEWER_BUNDLE_PATH, nothing selected."""
    monkeypatch.setenv(ENV_BUNDLE_PATH, str(tmp_path / "viewers"))
    monkeypatch.delenv(ENV_BUNDLE, raising=False)
    monkeypatch.delenv("ADA_VIEWER_STATIC_PATH", raising=False)
    return _bundle(tmp_path / "viewers")


def test_an_installed_bundle_is_found_and_unset_means_the_built_in_viewer(installed):
    bundle = list_viewer_bundles()["company"]
    assert bundle.plugins == ("capacity",)
    assert bundle.dist == installed / "dist"
    assert bundle.embed_zip == installed / "index.zip"
    assert resolve_viewer_bundle() is None
    assert resolve_viewer_bundle("builtin") is None


def test_a_directory_without_a_manifest_is_not_a_bundle(installed):
    (installed.parent / "stray").mkdir()
    assert set(list_viewer_bundles()) == {"company"}


def test_an_unknown_bundle_is_an_error_naming_what_is_installed(installed):
    with pytest.raises(ValueError, match='no viewer bundle "nope".*installed: company'):
        resolve_viewer_bundle("nope")


def test_the_rest_app_serves_the_selected_bundle(installed, monkeypatch):
    from ada.comms.rest.config import load_settings

    monkeypatch.setenv(ENV_BUNDLE, "company")
    assert load_settings().static_path == str(installed / "dist")
    # An explicit static path still wins.
    monkeypatch.setenv("ADA_VIEWER_STATIC_PATH", "/srv/viewer")
    assert load_settings().static_path == "/srv/viewer"


def test_show_opens_the_selected_bundles_single_file_build(installed, tmp_path, monkeypatch):
    from ada.visit.rendering import renderer_react

    monkeypatch.setattr(renderer_react, "THIS_DIR", tmp_path / "pkg")
    monkeypatch.setenv(ENV_BUNDLE, "company")
    renderer = renderer_react.RendererReact()
    assert renderer.local_html_path == tmp_path / "pkg" / "resources" / "bundles" / "company" / "index.html"
    assert renderer.local_html_path.read_text() == "<html>single file</html>"


def test_cli_viewer_selects_the_bundle_and_a_typo_fails_before_anything_starts(installed, monkeypatch):
    started = []
    # A stand-in for the server module: the real one imports uvicorn, which a core test env has not.
    server = types.ModuleType("ada.comms.rest.__main__")
    server.run = lambda: started.append(viewer_bundles.os.environ[ENV_BUNDLE])
    monkeypatch.setitem(sys.modules, "ada.comms.rest.__main__", server)
    assert ada_cli.main.main(["serve", "api", "--viewer", "company"]) == 0
    assert started == ["company"]
    with pytest.raises(ValueError, match="no viewer bundle"):
        ada_cli.main.main(["serve", "api", "--viewer", "compnay"])
    assert started == ["company"]


def test_cli_lists_the_installed_bundles(installed, capsys):
    assert ada_cli.main.main(["viewers"]) == 0
    out = capsys.readouterr().out
    assert "builtin" in out and "company" in out and "capacity" in out
