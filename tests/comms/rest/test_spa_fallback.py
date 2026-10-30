"""The viewer's SPA fallback: client routes get the shell, missing assets do not."""

import os
import tempfile

import pytest

# The app module builds its app at import: give it local storage, as the other REST tests do.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-spa-"))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import _wire_spa_fallback  # noqa: E402


@pytest.fixture
def client(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>shell</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("export {};")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    app = fastapi.FastAPI()
    _wire_spa_fallback(app, tmp_path)
    return TestClient(app)


def test_a_client_route_gets_the_shell_and_it_is_always_revalidated(client):
    r = client.get("/auth/callback")
    assert r.status_code == 200 and "shell" in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_a_hashed_asset_is_immutable(client):
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


@pytest.mark.parametrize(
    "path",
    ["/assets/index-gone.js", "/assets/style-gone.css", "/wasm/adacpp_render.js", "/lazy-gone.mjs", "/x.wasm"],
)
def test_a_missing_asset_is_a_404_not_the_shell(client, path):
    # A tab opened before a deploy asks for chunks the deploy removed: HTML in their place is
    # refused as a module ("Failed to load module script"); a 404 lets the page recover.
    assert client.get(path).status_code == 404


def test_other_files_and_the_api_behave_as_before(client):
    assert client.get("/favicon.svg").status_code == 200
    assert client.get("/api/nope").status_code == 404
