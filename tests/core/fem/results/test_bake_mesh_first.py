"""Mesh first: preliminary manifests while the bake runs, and the deferred SIN read.

``bake_artefacts(partial_manifests=True)`` emits a preliminary manifest as soon
as the mesh artefacts are out -- before any field is read -- and again as each
field completes; the final manifest replaces it. The SIN materialise reader
(``readers._make_sin_reader``) defers the field read until the bake first asks
for a field, so the mesh can be shipped before the fields are decoded.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from ada.fem.results.artefacts import bake_artefacts, read_manifest

from .test_bake_steps import _digests, _SyntheticReader

SIN_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "files"
    / "fem_files"
    / "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"
)


class _Watching(_SyntheticReader):
    """Records when the bake first asks for a field."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    def field_specs(self):
        self._log.append("<field_specs>")
        return super().field_specs()


def test_preliminary_manifests_go_out_mesh_first_then_field_by_field(tmp_path):
    log: list[str] = []
    snapshots: list[dict] = []
    stamps: list[dict] = []

    def sink(path: pathlib.Path) -> None:
        log.append(path.name)
        if path.name == "fea.manifest.json":
            snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        if path.name == "fea.stamp.json":
            stamps.append(json.loads(path.read_text(encoding="utf-8")))

    out = tmp_path / "bake"
    bake_artefacts(
        _Watching(log), out, src="s", on_artefact=sink, partial_manifests=True, stamp_extra={"job_id": "job-1"}
    )

    # The first manifest is out before the bake asked for a single field, right after
    # the mesh files, and it names them.
    first = log.index("fea.manifest.json")
    assert first < log.index("<field_specs>")
    assert {"fea.mesh.glb", "fea.mesh.edges.bin", "fea.mesh.elements.bin"} <= set(log[:first])
    prelim = snapshots[0]
    assert prelim["preliminary"] is True and prelim["fields"] == []
    assert prelim["mesh"]["url"] == "fea.mesh.glb"
    # It says what it is in its first bytes (a server reading only the head must see it).
    assert list(prelim)[:4] == ["version", "bake_version", "preliminary", "bake_job_id"]
    assert stamps[0]["preliminary"] is True and stamps[0]["job_id"] == "job-1"

    # Then once per completed field, each listing what is done and what is pending,
    # every manifest after the blobs it names and followed by its stamp.
    partial = [s for s in snapshots[1:] if s.get("preliminary")]
    assert [[f["name_canonical"] for f in s["fields"]] for s in partial] == [
        ["DISP"],
        ["DISP", "STRESS"],
        ["DISP", "STRESS", "props.thickness"],
    ]
    assert partial[0]["pending_fields"] == ["STRESS", "props.thickness"]
    assert [s.get("n_fields_ready") for s in stamps[1:4]] == [1, 2, 3]
    assert all(s.get("n_fields_total") == 3 for s in stamps[1:4])
    for i, name in enumerate(log):
        if name == "fea.manifest.json":
            assert log[i + 1] == "fea.stamp.json"
    disp_blob = log.index("fea.DISP.bin")
    assert disp_blob < [i for i, n in enumerate(log) if n == "fea.manifest.json"][1]

    # The final manifest is the ordinary one: no preliminary marks, everything in it.
    final = snapshots[-1]
    assert "preliminary" not in final and "pending_fields" not in final
    assert "preliminary" not in stamps[-1] and stamps[-1]["job_id"] == "job-1"
    assert log[-2:] == ["fea.manifest.json", "fea.stamp.json"]


def test_partial_manifests_change_nothing_the_final_bake_holds(tmp_path):
    plain, mesh_first = tmp_path / "plain", tmp_path / "first"
    bake_artefacts(_SyntheticReader(), plain, src="s", on_artefact=lambda p: None)
    bake_artefacts(_SyntheticReader(), mesh_first, src="s", on_artefact=lambda p: None, partial_manifests=True)
    assert _digests(plain) == _digests(mesh_first)


@pytest.mark.skipif(not SIN_PATH.exists(), reason="fixture not present")
def test_sin_reader_reads_the_mesh_before_the_fields(monkeypatch, tmp_path):
    from ada.fem.formats.sesam.results import read_sin
    from ada.fem.results.artefacts import make_stream_reader

    monkeypatch.setenv("ADA_FEA_SIN_STREAMER", "off")
    reads: list[int] = []
    real = read_sin._consume_fields

    def counting(*a, **kw):
        reads.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(read_sin, "_consume_fields", counting)
    with make_stream_reader(SIN_PATH) as reader:
        geom = reader.read_mesh_geometry()
        assert geom.points.shape[0] > 0
        assert reads == []  # the mesh came from the header records alone
        assert reader.field_specs()
        reader.field_specs()
        assert reads == [1]  # read once, on first demand

    # And the bake from it is the bake read_sin_file gave.
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.results.artefacts import FEAResultStreamAdapter

    monkeypatch.setattr(read_sin, "_consume_fields", real)
    with make_stream_reader(SIN_PATH) as reader:
        bake_artefacts(reader, tmp_path / "deferred", src="c")
    with FEAResultStreamAdapter(read_sin_file(SIN_PATH, field_cache=True)) as reader:
        bake_artefacts(reader, tmp_path / "eager", src="c")
    assert _digests(tmp_path / "deferred") == _digests(tmp_path / "eager")
    assert read_manifest(tmp_path / "deferred")["fields"]


@pytest.mark.skipif(not SIN_PATH.exists(), reason="fixture not present")
def test_deferred_reader_closed_unread_releases_the_file():
    from ada.fem.results.artefacts import make_stream_reader

    import os

    os.environ["ADA_FEA_SIN_STREAMER"] = "off"
    try:
        reader = make_stream_reader(SIN_PATH)
        reader.read_mesh_geometry()
        reader.close()  # no field ever asked for
        assert reader._load_results is None and reader._on_close is None
    finally:
        os.environ.pop("ADA_FEA_SIN_STREAMER", None)
