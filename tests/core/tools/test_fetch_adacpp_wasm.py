"""The dev server's wasm stays in step with what deploy/Dockerfile.viewer ships."""

import importlib.util
import json
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "packaging" / "fetch_adacpp_wasm.py"


@pytest.fixture(scope="module")
def fetch():
    spec = importlib.util.spec_from_file_location("fetch_adacpp_wasm", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def current(fetch, tmp_path):
    wanted = fetch.pins()
    for name in ("adacpp_extrude.js", *fetch._RENDER_FILES):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / fetch._PINS_FILE).write_text(json.dumps(wanted), encoding="utf-8")
    return wanted


def test_both_pins_read_from_the_viewer_dockerfile(fetch):
    wanted = fetch.pins()
    assert wanted["base_image"].startswith("ghcr.io/krande/adacpp-wasm-base:")
    assert len(wanted["render_ref"]) == 40


def test_a_missing_or_duplicated_pin_is_an_error(fetch):
    with pytest.raises(fetch.FetchError):
        fetch.pins("ARG ADACPP_BASE_IMAGE=a\n")
    with pytest.raises(fetch.FetchError):
        fetch.pins("ARG ADACPP_BASE_IMAGE=a\nARG ADACPP_RENDER_REF=b\nARG ADACPP_RENDER_REF=c\n")


def test_an_empty_dest_needs_both_parts(fetch, tmp_path):
    assert sorted(fetch.stale_parts(tmp_path, fetch.pins())) == ["base_image", "render_ref"]


def test_current_files_need_nothing(fetch, tmp_path, current):
    assert fetch.stale_parts(tmp_path, current) == []


def test_a_moved_render_ref_rebuilds_only_the_renderer(fetch, tmp_path, current):
    assert fetch.stale_parts(tmp_path, {**current, "render_ref": "0" * 40}) == ["render_ref"]


def test_a_deleted_module_is_fetched_again_under_an_unchanged_pin(fetch, tmp_path, current):
    (tmp_path / "adacpp_render.wasm").unlink()
    assert fetch.stale_parts(tmp_path, current) == ["render_ref"]


def test_if_stale_without_docker_warns_and_lets_the_dev_server_start(fetch, tmp_path, monkeypatch, capsys):
    def no_docker(*args):
        raise fetch.FetchError("docker is not on PATH")

    monkeypatch.setattr(fetch, "_docker", no_docker)
    assert fetch.main(["x", str(tmp_path), "--if-stale"]) == 0
    assert "WARNING" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        fetch.main(["x", str(tmp_path)])
