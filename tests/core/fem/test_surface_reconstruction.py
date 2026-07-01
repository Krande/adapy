"""FEM shell → B-spline surface reconstruction (opt-in ``reconstruct_surfaces``)."""

import numpy as np
import pytest

import ada
from ada.api.plates.base_pl import PlateCurved
from ada.fem.formats.surface_reconstruction import reconstruct_shell_surfaces

QUAD = ada.fem.Elem.EL_TYPES.SHELL_SHAPES.QUAD
TRI = ada.fem.Elem.EL_TYPES.SHELL_SHAPES.TRI


def _fit_supported() -> bool:
    """True when the active CAD backend implements the grid→bspline fit.
    Adacpp raises NotImplementedError (no native fit yet) → reconstruction
    falls back to flat plates, so the "expect a curved panel" tests are skipped
    there (the fallback path is covered by the dedicated fallback tests)."""
    from ada.cad import active_backend

    grid = [[(x, y, 0.0) for y in range(4)] for x in range(4)]
    try:
        active_backend().build_bspline_advanced_face_from_grid(grid, 1e-3)
        return True
    except NotImplementedError:
        return False
    except Exception:
        return True


requires_fit = pytest.mark.skipif(not _fit_supported(), reason="CAD backend has no grid→bspline surface fit (adacpp)")


def _add_quad(p, eid, ring, mat="S355", th=12e-3):
    el = p.fem.add_elem(ada.fem.Elem(eid, ring, QUAD))
    el.fem_sec = p.fem.add_section(
        ada.fem.FemSection(f"PlSec{eid}", "shell", ada.fem.FemSet(f"s{eid}", [el]), ada.Material(mat), thickness=th)
    )


def _add_tri(p, eid, ring, mat="S355", th=12e-3):
    el = p.fem.add_elem(ada.fem.Elem(eid, ring, TRI))
    el.fem_sec = p.fem.add_section(
        ada.fem.FemSection(f"PlSec{eid}", "shell", ada.fem.FemSet(f"s{eid}", [el]), ada.Material(mat), thickness=th)
    )


def _cylinder_nodes(p, nu, nv, R=2.0, h=3.0, nid0=1):
    """Structured quarter-cylinder node grid; returns {(i,j): Node}."""
    ng = {}
    nid = nid0
    for i in range(nu):
        ang = (i / (nu - 1)) * (np.pi / 2)
        for j in range(nv):
            n = ada.Node([R * np.cos(ang), R * np.sin(ang), (j / (nv - 1)) * h], nid)
            nid += 1
            ng[(i, j)] = n
            p.fem.nodes.add(n)
    return ng


def _quad_grid(p, ng, nu, nv, skip=(), eid0=1, **kw):
    """Wire a (nu-1)x(nv-1) quad grid over node grid ``ng``; ``skip`` omits cells."""
    eid = eid0
    for i in range(nu - 1):
        for j in range(nv - 1):
            if (i, j) in skip:
                continue
            ring = [ng[(i, j)], ng[(i + 1, j)], ng[(i + 1, j + 1)], ng[(i, j + 1)]]
            _add_quad(p, eid, ring, **kw)
            eid += 1
    return eid


def _split(objs):
    curved = [o for o in objs if isinstance(o, PlateCurved)]
    flat = [o for o in objs if not isinstance(o, PlateCurved)]
    return curved, flat


@requires_fit
def test_structured_cylinder_collapses_to_one_curved_panel():
    nu, nv = 13, 8
    p = ada.Part("panel")
    ng = _cylinder_nodes(p, nu, nv)
    _quad_grid(p, ng, nu, nv)
    ada.Assembly() / p

    objs = reconstruct_shell_surfaces(p, angle_tol=30.0, min_patch_quads=1)
    curved, flat = _split(objs)
    assert len(curved) == 1
    assert len(flat) == 0

    # fitted surface stays close to the input nodes
    af = curved[0].geom.geometry
    assert type(af).__name__ in ("AdvancedFace",)


@requires_fit
def test_reconstructed_panel_round_trips_to_step_and_ifc(tmp_path):
    nu, nv = 13, 8
    p = ada.Part("panel")
    ng = _cylinder_nodes(p, nu, nv)
    _quad_grid(p, ng, nu, nv)
    a = ada.Assembly() / p

    p.create_objects_from_fem(reconstruct_surfaces=True)
    curved, flat = _split(list(p.plates))
    assert len(curved) == 1 and len(flat) == 0

    step = tmp_path / "panel.step"
    p.to_stp(str(step))
    assert step.stat().st_size > 0

    ifc = tmp_path / "panel.ifc"
    a.to_ifc(str(ifc), validate=False)
    assert ifc.stat().st_size > 0


def test_triangles_fall_back_to_flat_plates():
    # Same region meshed with triangles — not QUAD, so no reconstruction.
    nu, nv = 6, 5
    p = ada.Part("tri")
    ng = _cylinder_nodes(p, nu, nv)
    eid = 1
    for i in range(nu - 1):
        for j in range(nv - 1):
            _add_tri(p, eid, [ng[(i, j)], ng[(i + 1, j)], ng[(i + 1, j + 1)]])
            eid += 1
            _add_tri(p, eid, [ng[(i, j)], ng[(i + 1, j + 1)], ng[(i, j + 1)]])
            eid += 1
    objs = reconstruct_shell_surfaces(p, angle_tol=30.0, min_patch_quads=1)
    curved, flat = _split(objs)
    assert len(curved) == 0
    assert len(flat) > 0


def test_cutout_is_not_a_rectangle_falls_back():
    # Punch an interior quad out → patch is no longer a full (nu-1)x(nv-1)
    # rectangle, so the grid recovery rejects it and keeps flat plates.
    nu, nv = 7, 7
    p = ada.Part("holed")
    ng = _cylinder_nodes(p, nu, nv)
    _quad_grid(p, ng, nu, nv, skip={(3, 3)})
    objs = reconstruct_shell_surfaces(p, angle_tol=30.0, min_patch_quads=1)
    curved, flat = _split(objs)
    assert len(curved) == 0
    assert len(flat) > 0


@requires_fit
def test_fold_splits_into_two_panels():
    # Two flat panels meeting at 90° share an edge. Region growing stops at the
    # fold (normals differ by 90° > angle_tol), so each side reconstructs
    # independently — no single surface spans the fold.
    p = ada.Part("fold")
    nid = 1
    nu, nv = 5, 4
    ng = {}
    # panel A in the z=0 plane (x,y), panel B in the x=const plane folding up (y,z)
    for i in range(nu):
        for j in range(nv):
            n = ada.Node([i * 0.5, j * 0.5, 0.0], nid)
            nid += 1
            ng[("A", i, j)] = n
            p.fem.nodes.add(n)
    # shared edge at i = nu-1 (x = (nu-1)*0.5): reuse those nodes for B's base row
    for i in range(1, nu):  # B extends in +z
        for j in range(nv):
            n = ada.Node([(nu - 1) * 0.5, j * 0.5, i * 0.5], nid)
            nid += 1
            ng[("B", i, j)] = n
            p.fem.nodes.add(n)
    for i in range(nu):
        for j in range(nv):
            ng[("B", 0, j)] = ng[("A", nu - 1, j)]  # shared fold edge

    eid = 1
    for tag in ("A", "B"):
        for i in range(nu - 1):
            for j in range(nv - 1):
                ring = [ng[(tag, i, j)], ng[(tag, i + 1, j)], ng[(tag, i + 1, j + 1)], ng[(tag, i, j + 1)]]
                _add_quad(p, eid, ring)
                eid += 1

    objs = reconstruct_shell_surfaces(p, angle_tol=30.0, min_patch_quads=1)
    curved, flat = _split(objs)
    assert len(curved) == 2
    assert len(flat) == 0


def test_backend_without_fit_uses_native_fallback(monkeypatch):
    # Backend fit absent (adacpp / OCC-absent): the verb raises NotImplementedError, and
    # _fit_patch now falls back to the OCC-free native degree-1 B-spline, so curved panels
    # are STILL reconstructed (a PlateCurved with a BSplineSurfaceWithKnots) — not dropped to
    # flat. This is what makes curved reconstruction work on the adacpp backend.
    from ada.geom.surfaces import BSplineSurfaceWithKnots

    nu, nv = 8, 6
    p = ada.Part("nofit")
    ng = _cylinder_nodes(p, nu, nv)
    _quad_grid(p, ng, nu, nv)

    from ada.cad import active_backend

    backend = active_backend()
    monkeypatch.setattr(
        backend,
        "build_bspline_advanced_face_from_grid",
        lambda *a, **k: (_ for _ in ()).throw(NotImplementedError()),
    )
    objs = reconstruct_shell_surfaces(p, angle_tol=30.0, min_patch_quads=1)
    curved, flat = _split(objs)
    assert len(curved) > 0  # native fallback reconstructs the curved panel(s)
    assert isinstance(curved[0].geom.geometry.face_surface, BSplineSurfaceWithKnots)


@requires_fit
def test_min_patch_quads_keeps_small_patches_flat():
    # A NURBS B-rep solid is heavier than a few flat plates, so patches below
    # the threshold stay flat; at/above it they reconstruct.
    nu, nv = 7, 5
    p = ada.Part("small")
    ng = _cylinder_nodes(p, nu, nv)
    _quad_grid(p, ng, nu, nv)
    n_quads = (nu - 1) * (nv - 1)

    flat_only = _split(reconstruct_shell_surfaces(p, min_patch_quads=n_quads + 1))
    assert len(flat_only[0]) == 0 and len(flat_only[1]) > 0

    curved_only = _split(reconstruct_shell_surfaces(p, min_patch_quads=n_quads))
    assert len(curved_only[0]) == 1 and len(curved_only[1]) == 0
