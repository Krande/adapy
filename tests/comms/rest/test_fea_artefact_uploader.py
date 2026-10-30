"""Mesh first: the worker's artefact uploader.

``ArtefactUploader`` ships the bake's files in emission order on its own thread,
snapshots the small JSON at submit (the bake re-writes its preliminary manifest
as fields land), and fails the bake on an upload failure instead of dropping the
artefact. Cancelling it stops it.
"""

from __future__ import annotations

import pathlib
import threading

import pytest

from ada.comms.rest.formats.fea import ArtefactUploader, BakeCancelled


def _files(tmp_path: pathlib.Path, names: list[str]) -> list[pathlib.Path]:
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(n.encode())
        out.append(p)
    return out


def test_uploader_keeps_emission_order_and_snapshots_json(tmp_path):
    uploaded: list[tuple[str, bytes]] = []
    seen_stamps: list[bytes] = []

    def upload(name, path, data, encoding):
        assert (encoding == "gzip") == name.endswith(".json")
        uploaded.append((name, data if data is not None else path.read_bytes()))

    up = ArtefactUploader(upload, lambda name, data: seen_stamps.append(data) if name == "fea.stamp.json" else None)
    paths = _files(tmp_path, ["fea.mesh.glb", "fea.manifest.json", "fea.stamp.json", "fea.F.bin"])
    for p in paths:
        up.submit(p)
    # The bake re-writes its preliminary manifest; the queued copy must not change.
    (tmp_path / "fea.manifest.json").write_bytes(b"rewritten")
    up.finish()
    assert [n for n, _ in uploaded] == ["fea.mesh.glb", "fea.manifest.json", "fea.stamp.json", "fea.F.bin"]
    assert dict(uploaded)["fea.manifest.json"] == b"fea.manifest.json"
    assert seen_stamps == [b"fea.stamp.json"]


def test_uploader_failure_fails_the_bake_and_drops_nothing_silently(tmp_path):
    failed = threading.Event()
    uploaded: list[str] = []

    def upload(name, path, data, encoding):
        if name == "fea.mesh.glb":
            failed.set()
            raise OSError("store refused")
        uploaded.append(name)

    up = ArtefactUploader(upload)
    a, b, c = _files(tmp_path, ["fea.mesh.glb", "fea.manifest.json", "fea.stamp.json"])
    up.submit(a)
    failed.wait(5)
    # Nothing behind the failure goes up, the next submit raises, and so does finish.
    with pytest.raises(RuntimeError, match="store refused"):
        for _ in range(100):
            up.submit(b)
            threading.Event().wait(0.01)
    with pytest.raises(RuntimeError, match="store refused"):
        up.finish()
    assert "fea.manifest.json" not in uploaded


def test_uploader_cancel(tmp_path):
    gate = threading.Event()
    uploaded: list[str] = []

    def upload(name, path, data, encoding):
        gate.wait(5)
        uploaded.append(name)

    up = ArtefactUploader(upload)
    for p in _files(tmp_path, ["fea.a.bin", "fea.b.bin", "fea.c.bin"]):
        up.submit(p)
    up.cancel()
    gate.set()
    with pytest.raises(BakeCancelled):
        up.finish()
    with pytest.raises(BakeCancelled):
        up.submit(tmp_path / "fea.a.bin")
    assert len(uploaded) <= 1
