"""The parallel worker-pool path (parse+build moved INTO the workers, dispatched by
root-id) must produce the same GLB as the in-process sequential path.

The pool path only triggers above ``_POOL_MIN_SOLIDS``; monkeypatch it low so a tiny
model exercises the multiprocess dispatch + worker-side ``build_one_solid``, then assert
the streamed GLB matches the sequential build byte-for-byte in the parts that matter
(meshed/total/skipped stats, triangle count, material count).
"""

import json
import struct

import pytest

import ada
from ada import Beam, Section
from ada.cadit.step.stream_to_glb import stream_step_to_glb


def _glb_stats(path) -> tuple[int, int]:
    b = path.read_bytes()
    jlen = struct.unpack("<I", b[12:16])[0]
    tree = json.loads(b[20 : 20 + jlen])
    # total triangle indices / 3 across all SCALAR (index) accessors, + material count
    tris = sum(a["count"] for a in tree.get("accessors", []) if a.get("type") == "SCALAR") // 3
    return tris, len(tree.get("materials", []))


def _model():
    # Several distinct solids (distinct sections + colours) so dispatch loops a few times
    # and the material store is exercised.
    p = ada.Part("p")
    for i in range(6):
        p / Beam(f"b{i}", (i, 0, 0), (i, 0, 3), Section(f"s{i}", from_str="IPE300"))
    return ada.Assembly("m") / p


def test_pool_path_matches_sequential(tmp_path, monkeypatch):
    pytest.importorskip("adacpp")
    monkeypatch.setenv("ADAPY_CAD_BACKEND", "adacpp")
    monkeypatch.setenv("ADA_STREAM_TESS_PIPELINE", "libtess2")

    src = tmp_path / "m.step"
    _model().to_stp(src)

    from ada.visit.scene_handling import scene_from_step_stream as sfs

    # Sequential reference: force the in-process path.
    monkeypatch.setattr(sfs, "_POOL_MIN_SOLIDS", 10_000, raising=True)
    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "1")
    seq_glb = tmp_path / "seq.glb"
    seq_stats = stream_step_to_glb(src, seq_glb, tolerant=True)

    # Pool path: tiny threshold + 2 workers so the multiprocess dispatch + worker-side
    # build_one_solid actually runs.
    monkeypatch.setattr(sfs, "_POOL_MIN_SOLIDS", 2, raising=True)
    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "2")
    pool_glb = tmp_path / "pool.glb"
    pool_stats = stream_step_to_glb(src, pool_glb, tolerant=True)

    # Stats identical (meshed/total/skipped accounting must match across paths).
    for k in ("meshed", "total", "skipped"):
        assert pool_stats[k] == seq_stats[k], f"{k}: pool={pool_stats[k]} seq={seq_stats[k]}"
    assert pool_stats["meshed"] == 6

    # GLB geometry identical (triangle + material counts).
    assert _glb_stats(pool_glb) == _glb_stats(seq_glb)


def test_pool_lpt_matches_sequential(tmp_path, monkeypatch):
    # LPT scheduling reorders dispatch (heaviest solid first) but must produce the same
    # GLB — only the order in which workers pick up solids changes.
    pytest.importorskip("adacpp")
    monkeypatch.setenv("ADAPY_CAD_BACKEND", "adacpp")
    monkeypatch.setenv("ADA_STREAM_TESS_PIPELINE", "libtess2")

    src = tmp_path / "m.step"
    _model().to_stp(src)

    from ada.visit.scene_handling import scene_from_step_stream as sfs

    monkeypatch.setattr(sfs, "_POOL_MIN_SOLIDS", 10_000, raising=True)
    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "1")
    seq_glb = tmp_path / "seq.glb"
    seq_stats = stream_step_to_glb(src, seq_glb, tolerant=True)

    monkeypatch.setattr(sfs, "_POOL_MIN_SOLIDS", 2, raising=True)
    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "2")
    monkeypatch.setenv("ADA_STEP_STREAM_LPT", "1")  # heaviest-first dispatch
    lpt_glb = tmp_path / "lpt.glb"
    lpt_stats = stream_step_to_glb(src, lpt_glb, tolerant=True)

    for k in ("meshed", "total", "skipped"):
        assert lpt_stats[k] == seq_stats[k]
    assert _glb_stats(lpt_glb) == _glb_stats(seq_glb)
