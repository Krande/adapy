"""Memory-bounded streaming STEP -> GLB (ada.cadit.step.stream_to_glb).

Streams the reader one solid at a time, tessellates via the active CAD backend, and
appends each mesh to the GLB — never holding the whole model. Runs under OCC and
adacpp (backend-neutral mesh contract).
"""

import json
import struct

import ada
from ada import Beam, Plate, Section
from ada.cadit.step.stream_to_glb import stream_step_to_glb


def _glb_tree(glb_bytes: bytes) -> dict:
    jlen = struct.unpack("<I", glb_bytes[12:16])[0]
    return json.loads(glb_bytes[20 : 20 + jlen])


def test_stream_step_to_glb_round_trip(tmp_path):
    a = ada.Assembly("m") / (
        ada.Part("p")
        / [
            Beam("ipe", (0, 0, 0), (3, 0, 0), Section("ipe", from_str="IPE300")),
            Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02),
        ]
    )
    src = tmp_path / "m.step"
    a.to_stp(src)  # OCC writer (forward references -> two-pass reader path)

    glb = tmp_path / "m.glb"
    stats = stream_step_to_glb(src, glb, tolerant=True)

    assert stats["meshed"] >= 2
    assert glb.exists() and glb.stat().st_size > 0

    # the GLB loads back as a scene with the meshed geometry
    import trimesh

    scene = trimesh.load(glb)
    assert sum(len(g.faces) for g in scene.geometry.values()) > 0


def test_stream_step_to_glb_extracts_colour_merges_and_emits_ada_ext(tmp_path):
    # The streamed GLB must match the normal to_gltf shape: per-solid STEP colours are
    # extracted, meshes are merged by colour (one material per colour), and the ADA
    # design-extension is emitted for picking.
    from ada.visit.colors import Color

    box = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    box.color = Color(1.0, 0.0, 0.0)
    cyl = ada.PrimCyl("cy", (2, 0, 0), (2, 0, 1), 0.4)
    cyl.color = Color(0.0, 0.0, 1.0)
    src = tmp_path / "colored.step"
    (ada.Assembly("m") / (ada.Part("p") / [box, cyl])).to_stp(src)

    glb = tmp_path / "colored.glb"
    stats = stream_step_to_glb(src, glb)

    assert stats["meshed"] == 2
    assert stats["materials"] == 2  # two distinct colours -> two merged material groups

    tree = _glb_tree(glb.read_bytes())
    assert len(tree["materials"]) == 2  # merged by colour, not one node per solid
    assert "ADA_EXT_data" in tree.get("extensionsUsed", [])  # design tree for picking
    # red box colour survives the STEP round-trip into a material baseColorFactor
    base_colors = [m.get("pbrMetallicRoughness", {}).get("baseColorFactor") for m in tree["materials"]]
    assert [1.0, 0.0, 0.0, 1.0] in base_colors


def test_stream_workers_reserves_a_core_for_the_event_loop(monkeypatch):
    # The tessellation pool must leave one CPU free: when this runs inside the
    # conversion worker, the parent asyncio loop has to refresh the JetStream
    # in_progress lease (every 30s, within a 180s ack_wait). A pool that pins every
    # core starves that loop -> lease expires -> the still-running job is redelivered
    # -> another conversion + pool spawns -> redelivery cascade. So workers == cpus-1.
    from ada.visit.scene_handling import scene_from_step_stream as sfss

    monkeypatch.delenv("ADA_STEP_STREAM_WORKERS", raising=False)
    monkeypatch.setattr(sfss, "_cgroup_cpu_quota", lambda: 4)
    assert sfss._stream_workers() == 3  # 4-core pod -> 3 workers, 1 core for the loop
    monkeypatch.setattr(sfss, "_cgroup_cpu_quota", lambda: 2)
    assert sfss._stream_workers() == 1
    monkeypatch.setattr(sfss, "_cgroup_cpu_quota", lambda: 1)
    assert sfss._stream_workers() == 1  # never below 1

    # An explicit override is honoured verbatim (operator owns the trade-off).
    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "6")
    assert sfss._stream_workers() == 6


def _total_tris(glb_bytes: bytes) -> int:
    tree = _glb_tree(glb_bytes)
    return sum(tree["accessors"][p["indices"]]["count"] // 3 for m in tree.get("meshes", []) for p in m["primitives"])


def test_stream_pool_matches_sequential(monkeypatch, tmp_path):
    # The self-managed timeout pool must produce byte-for-byte the same meshes as the
    # sequential path — same solids, same triangle count.
    from ada.visit.scene_handling import scene_from_step_stream as sfss

    monkeypatch.setattr(sfss, "_POOL_MIN_SOLIDS", 4)  # force the pool on a small model
    parts = [Beam(f"b{i}", (i, 0, 0), (i, 0, 3), Section("t", from_str="TUB300x20")) for i in range(8)]
    src = tmp_path / "m.step"
    (ada.Assembly("m") / (ada.Part("p") / parts)).to_stp(src)

    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "1")  # sequential (n_workers=1 -> no pool)
    seq = stream_step_to_glb(src, tmp_path / "seq.glb")
    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "3")  # parallel pool
    par = stream_step_to_glb(src, tmp_path / "par.glb")

    assert seq["meshed"] == par["meshed"] == 8
    assert _total_tris((tmp_path / "seq.glb").read_bytes()) == _total_tris((tmp_path / "par.glb").read_bytes())


def test_stream_pool_per_solid_timeout_skips_hung_solid(monkeypatch, tmp_path):
    # A solid that hangs OCC tessellation must be killed at the per-solid budget and
    # skipped, so the conversion COMPLETES instead of freezing the whole job forever.
    from ada.visit.scene_converter import SceneConverter
    from ada.visit.scene_handling import scene_from_step_stream as sfss
    from ada.visit.scene_handling.scene_from_step_stream import StepStreamSource

    monkeypatch.setattr(sfss, "_POOL_MIN_SOLIDS", 2)
    parts = [Beam(f"b{i}", (i, 0, 0), (i, 0, 3), Section("t", from_str="TUB300x20")) for i in range(6)]
    src = tmp_path / "m.step"
    (ada.Assembly("m") / (ada.Part("p") / parts)).to_stp(src)

    monkeypatch.setenv("ADA_STEP_STREAM_WORKERS", "2")
    monkeypatch.setenv("ADA_STEP_STREAM_TEST_HANG_S", "3")  # every solid hangs 3s
    monkeypatch.setenv("ADA_STEP_STREAM_SOLID_TIMEOUT_S", "0.5")  # killed at 0.5s

    scene = SceneConverter(source=StepStreamSource(src, tolerant=True)).build_scene()
    stats = scene.metadata["ada_stream_stats"]
    assert stats["meshed"] == 0  # every solid hung -> all killed + skipped
    assert any("timeout" in r for r in stats["reasons"])  # reaped by the per-solid timeout
