import re
import numpy as np
import pytest

import ada


def _world(bm: ada.Beam, pt) -> tuple:
    """The beam endpoint in world coords: apply the full placement transform to the
    LOCAL n1/n2. These beams carry a rotated ObjectPlacement (local Z extruded onto
    world X), so the placement rotation must be applied — a naive ``origin + n1`` does
    not, and would silently mask a wrong-axis placement (beam-standard-case.ifc)."""
    m = bm.placement.get_matrix4x4()
    q = m @ np.array([pt[0], pt[1], pt[2], 1.0])
    return tuple(np.round(q[:3], 4))


def test_read_standard_case_beams(example_files, tmp_path):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-standard-case.ifc")

    a.to_ifc(tmp_path / "beam-standard-case-re-exported.ifc")

    p = a.get_by_name("Building")
    assert len(p.beams) == 18

    # World-space beam axes, verified against ifcopenshell's create_shape (USE_WORLD_COORDS).
    # The A-row beams run along world X at increasing Y; the placement swaps local Z -> world X.
    bm_a1: ada.Beam = p.get_by_name("A-1")
    assert _world(bm_a1, bm_a1.n1.p) == (0.0, -0.055, 0.11)
    assert _world(bm_a1, bm_a1.n2.p) == (2.0, -0.055, 0.11)

    bm_a2: ada.Beam = p.get_by_name("A-2")
    assert _world(bm_a2, bm_a2.n1.p) == (0.0, 1.5, 0.11)
    assert _world(bm_a2, bm_a2.n2.p) == (2.0, 1.5, 0.11)

    bm_b1: ada.Beam = p.get_by_name("B-1")
    assert _world(bm_b1, bm_b1.n1.p) == pytest.approx((-0.0149, -0.0387, 1.5976), abs=1e-3)
    assert _world(bm_b1, bm_b1.n2.p) == pytest.approx((2.9249, 0.2043, 2.1436), abs=1e-3)


def test_read_extruded_solid_beams(example_files):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-extruded-solid.ifc")
    p = a.get_part("Grasshopper Building")
    assert len(p.beams) == 1
    bm = p.beams[0]

    # n1/n2 are in the beam's LOCAL frame (the extrusion is along local Z); the ObjectPlacement
    # rotates that onto world +Y. Check the WORLD endpoints via the placement transform.
    assert tuple(_world(bm, bm.n1.p)) == (0.0, 0.0, 0.0)
    assert tuple(_world(bm, bm.n2.p)) == (0.0, 10.0, 0.0)


def test_read_varying_cardinal_points(example_files):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-varying-cardinal-points.ifc")
    p = a.get_part("IfcBuilding")
    assert len(p.beams) == 4
    bm = p.beams[0]
    print(bm)
    # Todo: import and check the cardinal points


def test_read_varying_extrusion_path(example_files):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-varying-extrusion-paths.ifc")
    _ = a.to_ifc(file_obj_only=True)
    print(a)


def test_read_revolved_solid(example_files):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-revolved-solid.ifc")
    _ = a.to_ifc(file_obj_only=True)
    print(a)


def test_extruded_solid_beam_winding_is_outward(example_files, monkeypatch):
    """The production NGEOM libtess2 stream tessellates the extruded beam with OUTWARD-facing
    normals (positive signed volume). A CW-discretized profile loop previously came out
    inside-out (negative volume -> dark shading). Gated to ada-cpp (libtess2 needs it)."""
    import trimesh

    from ada.cad import active_backend

    if active_backend().name != "adacpp":
        pytest.skip("NGEOM libtess2 stream tessellation is the ada-cpp path")

    monkeypatch.setenv("ADA_STREAM_TESS_PIPELINE", "libtess2")
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-extruded-solid.ifc")
    bm = next(iter(a.get_all_physical_objects()))
    sc = a.to_trimesh_scene(merge_meshes=True)
    m = trimesh.util.concatenate([g for g in sc.geometry.values() if hasattr(g, "faces")])
    # Right magnitude (area * length) AND positive sign (outward winding).
    # Sharp-I ``Ax`` underestimates the real (filleted) IPE600 area by ~4%, so the tessellated
    # solid — which now includes the web/flange fillets — sits a few % above ``Ax*length``.
    # Band-check the magnitude (catches gross errors) but keep the strict positive-volume
    # winding guard.
    exp = bm.section.properties.Ax * 10.0  # 10 m long
    assert m.volume > 0, f"extrusion is inside-out (negative volume {m.volume:.5f})"
    assert exp * 0.97 < m.volume < exp * 1.10, f"volume {m.volume:.5f} out of band around {exp:.5f}"


def test_read_varying_cardinal_points_world_positions(example_files):
    """The 4 beams in beam-varying-cardinal-points.ifc each carry a different CardinalPoint
    (1/2/8/9). The authoring tool bakes that offset into the extrusion's Position, and each
    beam's rotated ObjectPlacement (no PlacementRelTo) must be applied — the old no-parent
    import path dropped the placement, collapsing every profile onto its axis. World bboxes
    verified against ifcopenshell.geom (USE_WORLD_COORDS)."""
    import numpy as np
    import trimesh

    a = ada.from_ifc(example_files / "ifc_files/beams/beam-varying-cardinal-points.ifc")
    # (min, max) world bbox per beam name.
    expected = {
        "BotLeft": ((0.5, 0.0, 0.0), (0.6, 1.0, 0.2)),
        "BotMid": ((-0.05, 0.0, 0.0), (0.05, 1.0, 0.2)),
        "TopMid": ((-0.05, 0.0, -0.2), (0.05, 1.0, 0.0)),
        "TopRight": ((0.4, 0.0, -0.2), (0.5, 1.0, 0.0)),
    }
    sc = a.to_trimesh_scene(merge_meshes=False)
    seen = {}
    for node in sc.graph.nodes_geometry:
        m = re.search(r"name=(\w+)", str(node))
        if not m:
            continue
        T, gn = sc.graph[node]
        v = trimesh.transformations.transform_points(np.asarray(sc.geometry[gn].vertices), T)
        seen[m.group(1)] = (v.min(0), v.max(0))
    for nm, (emn, emx) in expected.items():
        assert nm in seen, f"{nm} not rendered"
        amn, amx = seen[nm]
        assert np.allclose(amn, emn, atol=1e-3) and np.allclose(amx, emx, atol=1e-3), (
            f"{nm}: cardinal offset wrong — got {np.round(amn,3)}..{np.round(amx,3)}, want {emn}..{emx}"
        )
