import json
import subprocess
import sys
import textwrap

import pytest

from ada.api.connections.library import (
    CONFIG_FILENAME,
    Library,
    LibraryConfigError,
    load_config,
)
from ada.api.connections.spec import _clear_registry, list_specs

SPEC_TEMPLATE = """
from ada.api.connections import ConnectionSpec, MemberCriteria, MemberKind, MemberRole, register_connection

SPEC = ConnectionSpec(
    name="{name}",
    roles=(
        MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.PLATE),
        MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({{"BOX"}})),
    ),
)


@register_connection(SPEC)
def build(incoming, landing, **_):
    import logging

    logging.getLogger("devlib").warning("{message}")
    return None
"""


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    _clear_registry()
    before = set(sys.modules)
    yield
    _clear_registry()
    for name in set(sys.modules) - before:
        if name.startswith("devlib"):
            del sys.modules[name]


def _write_library(root, name="devlib.plate_on_box", message="first"):
    pkg = root / "devlib"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "plate_on_box.py").write_text(SPEC_TEMPLATE.format(name=name, message=message))
    (root / CONFIG_FILENAME).write_text(
        textwrap.dedent(
            """
            [library]
            name = "dev library"
            package = "devlib"
            python_path = ["."]
            """
        )
    )


def test_load_config_resolves_paths(tmp_path):
    _write_library(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.name == "dev library"
    assert cfg.packages == ("devlib",)
    assert cfg.watch == (tmp_path.resolve(),)
    assert cfg.python_path == (tmp_path.resolve(),)


def test_load_config_errors(tmp_path):
    with pytest.raises(LibraryConfigError, match="no such file"):
        load_config(tmp_path)
    (tmp_path / CONFIG_FILENAME).write_text("[library]\nname = 'x'\n")
    with pytest.raises(LibraryConfigError, match="needs a `package` or a `loader`"):
        load_config(tmp_path)


def test_load_reload_and_build(tmp_path):
    _write_library(tmp_path)
    lib = Library(load_config(tmp_path))

    result = lib.load()
    assert result.ok, result.traceback
    assert "devlib.plate_on_box" in result.modules
    specs = lib.specs()
    assert [s["name"] for s in specs] == ["devlib.plate_on_box"]
    spec = specs[0]
    assert spec["in_library"] is True
    assert spec["defaults"]["incoming"] == {"thickness": 0.02, "width": 0.4, "height": 0.3}
    assert spec["source"]["file"].endswith("plate_on_box.py")

    glb = tmp_path / "out.glb"
    report = lib.build("devlib.plate_on_box", {"incoming": {"thickness": 0.03}}, glb)
    assert glb.stat().st_size > 0
    assert report["inputs"]["incoming"] == {"thickness": 0.03, "width": 0.4, "height": 0.3}
    assert report["counts"] == {"beams": 1, "plates": 1, "welds": 0}
    assert {"level": "WARNING", "logger": "devlib", "message": "first"} in report["log"]

    # An edit — even one that renames the spec — is picked up by reload, without duplicate names.
    _write_library(tmp_path, name="devlib.renamed", message="second")
    result = lib.reload()
    assert result.ok, result.traceback
    assert [s.name for s in list_specs()] == ["devlib.renamed"]
    report = lib.build("devlib.renamed", {}, glb)
    assert report["log"][-1]["message"] == "second"


def test_a_same_size_edit_within_the_second_is_not_served_from_stale_bytecode(tmp_path):
    _write_library(tmp_path, message="aaaa")
    lib = Library(load_config(tmp_path))
    assert lib.load().ok
    _write_library(tmp_path, message="bbbb")  # same size, same second
    assert lib.reload().ok
    report = lib.build("devlib.plate_on_box", {}, tmp_path / "out.glb")
    assert report["log"][-1]["message"] == "bbbb"


def test_a_broken_edit_is_reported_and_the_next_one_recovers(tmp_path):
    _write_library(tmp_path)
    lib = Library(load_config(tmp_path))
    assert lib.load().ok

    (tmp_path / "devlib" / "plate_on_box.py").write_text("def broken(:\n")
    result = lib.reload()
    assert not result.ok
    assert "SyntaxError" in result.error
    assert list_specs() == []

    _write_library(tmp_path)
    assert lib.reload().ok
    assert [s.name for s in list_specs()] == ["devlib.plate_on_box"]


def test_devhost_protocol(tmp_path):
    _write_library(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "ada.api.connections.devhost", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready" and ready["ok"], ready
        assert [s["name"] for s in ready["specs"]] == ["devlib.plate_on_box"]

        glb = tmp_path / "host.glb"
        proc.stdin.write(json.dumps({"id": 1, "op": "build", "spec_name": "devlib.plate_on_box", "glb_path": str(glb)}))
        proc.stdin.write("\n")
        proc.stdin.flush()
        reply = json.loads(proc.stdout.readline())
        assert reply["id"] == 1 and reply["ok"], reply
        assert glb.stat().st_size > 0

        proc.stdin.write(json.dumps({"id": 2, "op": "build", "spec_name": "nope", "glb_path": str(glb)}) + "\n")
        proc.stdin.flush()
        reply = json.loads(proc.stdout.readline())
        assert reply["id"] == 2 and not reply["ok"]
        assert "nope" in reply["error"]

        proc.stdin.write(json.dumps({"id": 3, "op": "quit"}) + "\n")
        proc.stdin.flush()
        assert json.loads(proc.stdout.readline())["id"] == 3
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
