"""Streaming SIF bake reader (`SifStreamReader`) + its env gate.

The streaming reader keeps one step resident at a time (via the byte-offset
index) so a many-mode SIF can bake without materialising the whole result. It
must produce *exactly* what the default full-materialise adapter produces —
these tests pin that equivalence at the reader level (mesh, specs, every
per-step value) and end-to-end (the bake's field blobs are byte-identical with
the flag off vs on). The gate (`ADA_FEA_SIF_STREAMER`) defaults off.
"""

from __future__ import annotations

import numpy as np

from ada.fem.formats.sesam.results.read_sif import read_sif_file
from ada.fem.formats.sesam.results.sif_stream import SifStreamReader
from ada.fem.results.artefacts import (
    FEAResultStreamAdapter,
    bake_fea_artefacts_from_source,
    make_stream_reader,
)

_EIGEN = "cantilever/sesam/eigen/shell/EIGEN_SHELL_CANTILEVER_SESAMR1.SIF"  # 20 steps


def test_stream_reader_mesh_and_specs_match_adapter(fem_files):
    sif = fem_files / _EIGEN
    ref = FEAResultStreamAdapter(read_sif_file(sif))
    strm = SifStreamReader(sif)

    g1, g2 = ref.read_mesh_geometry(), strm.read_mesh_geometry()
    assert g1.points.shape == g2.points.shape
    assert np.allclose(g1.points, g2.points)

    assert {s.name for s in ref.field_specs()} == {s.name for s in strm.field_specs()}
    # The streamer advertises the global step count, not a single step's.
    for s in strm.field_specs():
        assert s.n_steps == 20
    assert {(s.name, s.elem_type) for s in ref.element_field_specs()} == {
        (s.name, s.elem_type) for s in strm.element_field_specs()
    }


def test_stream_reader_nodal_values_match_adapter(fem_files):
    sif = fem_files / _EIGEN
    ref = FEAResultStreamAdapter(read_sif_file(sif))
    strm = SifStreamReader(sif)
    for spec in strm.field_specs():
        if spec.support != "nodal":
            continue
        a = list(ref.iter_field_steps(spec.name))
        b = list(strm.iter_field_steps(spec.name))
        assert len(a) == len(b) == 20
        for x, y in zip(a, b):
            assert x.values.shape == y.values.shape
            assert np.allclose(x.values, y.values)


def test_stream_reader_element_values_match_adapter(fem_files):
    sif = fem_files / _EIGEN
    ref = FEAResultStreamAdapter(read_sif_file(sif))
    strm = SifStreamReader(sif)
    ref_specs = {(s.name, s.elem_type): s for s in ref.element_field_specs()}
    strm_specs = strm.element_field_specs()
    assert strm_specs  # the eigen shell deck has element STRESS
    for spec in strm_specs:
        rspec = ref_specs[(spec.name, spec.elem_type)]
        assert spec.element_labels == rspec.element_labels  # order is load-bearing
        a = list(ref.iter_element_field_steps(rspec))
        b = list(strm.iter_element_field_steps(spec))
        assert len(a) == len(b) == 20
        for x, y in zip(a, b):
            assert x.values.shape == y.values.shape
            assert np.allclose(x.values, y.values)


def test_make_sif_reader_defaults_to_streaming(fem_files, monkeypatch):
    sif = fem_files / _EIGEN
    monkeypatch.delenv("ADA_FEA_SIF_STREAMER", raising=False)
    with make_stream_reader(sif) as r:
        assert isinstance(r, SifStreamReader)  # streaming is the default now

    monkeypatch.setenv("ADA_FEA_SIF_STREAMER", "off")
    with make_stream_reader(sif) as r:
        assert isinstance(r, FEAResultStreamAdapter)  # explicit opt-out


def test_stream_reader_nodal_labels_lis_enriched(fem_files):
    # The eigen fixture ships a sibling SESTRA.LIS; the streamer must reproduce
    # the full path's eigen-frequency step labels for nodal fields (not the
    # bare mode index), and leave element fields on the step index.
    sif = fem_files / _EIGEN
    ref = FEAResultStreamAdapter(read_sif_file(sif))
    strm = SifStreamReader(sif)

    ref_n = {s.name: s.step_values for s in ref.field_specs() if s.support == "nodal"}
    strm_n = {s.name: s.step_values for s in strm.field_specs() if s.support == "nodal"}
    assert ref_n and ref_n.keys() == strm_n.keys()
    for name in ref_n:
        assert np.allclose(ref_n[name], strm_n[name])
    # LIS was actually applied: the labels are frequencies, not 1..20.
    assert not np.allclose(strm_n["RVNODDIS"], np.arange(1, 21, dtype=float))

    ref_e = {(s.name, s.elem_type): s.step_values for s in ref.element_field_specs()}
    strm_e = {(s.name, s.elem_type): s.step_values for s in strm.element_field_specs()}
    for key in ref_e:
        assert np.allclose(ref_e[key], strm_e[key])


def test_bake_blobs_identical_streaming_vs_full(tmp_path, fem_files, monkeypatch):
    # End-to-end: the bake's field blobs AND the manifest field step_values
    # (LIS-enriched) must match between the streamer and the full-materialise
    # adapter — the streamer only changes memory, not output.
    import json

    sif = fem_files / _EIGEN

    monkeypatch.setenv("ADA_FEA_SIF_STREAMER", "off")  # force full-materialise
    bake_full = bake_fea_artefacts_from_source(sif, tmp_path / "full", src_key=sif.stem)

    monkeypatch.delenv("ADA_FEA_SIF_STREAMER", raising=False)  # default = streaming
    bake_strm = bake_fea_artefacts_from_source(sif, tmp_path / "stream", src_key=sif.stem)

    def blobs(d):
        return sorted(p.name for p in d.iterdir() if p.suffix == ".bin")

    names = blobs(bake_full.out_dir)
    assert names and names == blobs(bake_strm.out_dir)
    for name in names:
        a = (bake_full.out_dir / name).read_bytes()
        b = (bake_strm.out_dir / name).read_bytes()
        assert a == b, f"blob {name} differs between full and streaming bake"

    def field_steps(bake):
        m = json.loads(bake.manifest_path.read_text())
        return {f["name_canonical"]: [s["value"] for s in f["steps"]] for f in m["fields"]}

    full_steps, strm_steps = field_steps(bake_full), field_steps(bake_strm)
    assert full_steps and full_steps.keys() == strm_steps.keys()
    for name in full_steps:
        assert np.allclose(full_steps[name], strm_steps[name]), f"step values differ for {name}"
    # The nodal field carries LIS eigen-frequencies, not the bare 1..20 index.
    nodal = next(v for k, v in strm_steps.items() if not np.allclose(v, np.arange(1, 21)))
    assert nodal[0] > 1.5
