# Sweep example — backend-neutral reference implementation.
# Builds swept solids from point arrays with consistent profile alignment by
# constructing an ada.geom FixedReferenceSweptAreaSolid and building it through
# the active CAD backend (works under pythonocc AND adacpp). It is an independent
# reference (own profile-orientation math, not ada.PrimSweep's placement code) that
# the swept-area tests cross-check ada.PrimSweep against. No OCC import here.
# Run this file directly to visualize the 3 sweeps if you have a GUI environment.
from __future__ import annotations

from typing import List, Optional, Tuple

from ada.geom.points import Point

wt = 8e-3
# 2D profile (triangle/fillet) in local profile coordinates
fillet = [(0, 0), (-wt, 0), (0, wt)]

# ----------------------
# Input paths (many points)
# ----------------------
sweep1_pts = [
    [287.85, 99.917, 513.26],
    [287.85, 100.083, 513.26],
    [287.85, 100.08950561835023, 513.2587059520527],
    [287.85, 100.09502081528021, 513.2550208152801],
    [287.85, 100.09870595205274, 513.2495056183502],
    [287.85, 100.10000000000005, 513.2429999999999],
    [287.85, 100.1, 513.077],
    [287.85, 100.09870595205268, 513.0704943816498],
    [287.85, 100.09502081528017, 513.0649791847198],
    [287.85, 100.0895056183502, 513.0612940479473],
    [287.85, 100.083, 513.06],
    [287.85, 99.917, 513.06],
    [287.85, 99.91049438164977, 513.0612940479473],
    [287.85, 99.90497918471979, 513.0649791847198],
    [287.85, 99.90129404794726, 513.0704943816497],
    [287.85, 99.89999999999995, 513.077],
    [287.85, 99.9, 513.2429999999999],
    [287.85, 99.90129404794732, 513.2495056183501],
    [287.85, 99.90497918471983, 513.2550208152801],
    [287.85, 99.9104943816498, 513.2587059520527],
    [287.85, 99.917, 513.26],
]

sweep2_pts = [
    [287.833, 100.1, 513.26],
    [287.667, 100.1, 513.26],
    [287.66049438164976, 100.1, 513.2587059520527],
    [287.65497918471976, 100.1, 513.2550208152801],
    [287.65129404794726, 100.1, 513.2495056183502],
    [287.6499999999999, 100.1, 513.2429999999999],
    [287.65, 100.1, 513.077],
    [287.65129404794726, 100.1, 513.0704943816498],
    [287.6549791847198, 100.1, 513.0649791847198],
    [287.66049438164976, 100.1, 513.0612940479473],
    [287.667, 100.1, 513.06],
    [287.833, 100.1, 513.06],
    [287.83950561835024, 100.1, 513.0612940479473],
    [287.84502081528024, 100.1, 513.0649791847198],
    [287.84870595205274, 100.1, 513.0704943816497],
    [287.8500000000001, 100.1, 513.077],
    [287.85, 100.1, 513.2429999999999],
    [287.84870595205274, 100.1, 513.2495056183501],
    [287.8450208152802, 100.1, 513.2550208152801],
    [287.83950561835024, 100.1, 513.2587059520527],
    [287.833, 100.1, 513.26],
]

sweep3_pts = [
    [287.833, 99.9, 513.26],
    [287.667, 99.9, 513.26],
    [287.66049438164976, 99.9, 513.2587059520527],
    [287.65497918471976, 99.9, 513.2550208152801],
    [287.65129404794726, 99.9, 513.2495056183502],
    [287.6499999999999, 99.9, 513.2429999999999],
    [287.65, 99.9, 513.077],
    [287.65129404794726, 99.9, 513.0704943816498],
    [287.6549791847198, 99.9, 513.0649791847198],
    [287.66049438164976, 99.9, 513.0612940479473],
    [287.667, 99.9, 513.06],
    [287.833, 99.9, 513.06],
    [287.83950561835024, 99.9, 513.0612940479473],
    [287.84502081528024, 99.9, 513.0649791847198],
    [287.84870595205274, 99.9, 513.0704943816497],
    [287.8500000000001, 99.9, 513.077],
    [287.85, 99.9, 513.2429999999999],
    [287.84870595205274, 99.9, 513.2495056183501],
    [287.8450208152802, 99.9, 513.2550208152801],
    [287.83950561835024, 99.9, 513.2587059520527],
    [287.833, 99.9, 513.26],
]


# ----------------------
# Backend-neutral sweep construction
# ----------------------


def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    from math import sqrt

    x, y, z = v
    n = sqrt(x * x + y * y + z * z)
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (x / n, y / n, z / n)


def _cross(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    ax, ay, az = a
    bx, by, bz = b
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    ax, ay, az = a
    bx, by, bz = b
    return ax * bx + ay * by + az * bz


def _profile_frame(
    normal: Tuple[float, float, float],
    ydir: Optional[Tuple[float, float, float]] = None,
    xdir: Optional[Tuple[float, float, float]] = None,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
    """Orthonormal (x, y, n) frame for embedding a 2D profile in 3D.

    - If xdir is provided, it is used as the X of the local frame.
    - Else if ydir is provided, X is computed as cross(ydir, normal).
    - Y is recomputed as cross(normal, X) to ensure orthonormality.
    """
    n = _normalize(normal)
    if xdir is not None:
        x = _normalize(xdir)
        if abs(_dot(x, n)) > 0.999:
            raise ValueError("profile_xdir nearly parallel to profile_normal")
        y = _cross(n, x)
    else:
        if ydir is None:
            ydir = (0.0, 0.0, 1.0)
        y0 = _normalize(ydir)
        if abs(_dot(y0, n)) > 0.999:
            y0 = (1.0, 0.0, 0.0)
        x = _normalize(_cross(y0, n))
        y = _normalize(_cross(n, x))
    return x, y, n


def _embed_profile_3d(poly2d, origin, x, y) -> List[Tuple[float, float, float]]:
    """Embed 2D profile points in the plane (origin, x, y) → absolute 3D points."""
    ox, oy, oz = origin
    return [
        (ox + u * x[0] + v * y[0], oy + u * x[1] + v * y[1], oz + u * x[2] + v * y[2]) for (u, v) in poly2d
    ]


def make_wire_from_points(pts):
    """Open polyline wire through the given 3D points (backend-neutral)."""
    from ada.cad import active_backend

    assert len(pts) >= 2, "Need at least two points for a wire"
    return active_backend().make_wire([[float(c) for c in p] for p in pts])


def make_profile_wire(
    poly2d: List[Tuple[float, float]],
    origin: Tuple[float, float, float],
    normal: Tuple[float, float, float],
    ydir: Optional[Tuple[float, float, float]] = None,
    xdir: Optional[Tuple[float, float, float]] = None,
):
    """Closed profile wire from 2D points embedded in a plane at ``origin``.

    Returns ``(wire_handle, x, y, n)``. The wire is built through the active CAD
    backend, so the result is a backend shape handle (not an OCC wire).
    """
    from ada.cad import active_backend

    x, y, n = _profile_frame(normal, ydir=ydir, xdir=xdir)
    pts3d = _embed_profile_3d(poly2d, origin, x, y)
    closed = pts3d + [pts3d[0]]  # ensure closed
    return active_backend().make_wire([list(p) for p in closed]), x, y, n


def sweep_profile_along_path(
    path_pts,
    profile2d: List[Tuple[float, float]],
    profile_normal: Tuple[float, float, float],
    profile_ydir: Optional[Tuple[float, float, float]] = None,
    profile_xdir: Optional[Tuple[float, float, float]] = None,
):
    """Sweep a 2D profile along a 3D path, returning a backend solid handle.

    Builds an ada.geom ``FixedReferenceSweptAreaSolid`` (profile embedded in 3D at
    the path start, swept along the directrix) and realises it through the active
    backend. Under pythonocc this is the same MakePipeShell(RoundCorner)+MakeSolid
    pipeline as the historical OCC reference; under adacpp it uses the native
    swept-area builder.
    """
    from ada.cad import active_backend
    from ada.geom import Geometry
    from ada.geom.curves import Edge, IndexedPolyCurve
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.solids import FixedReferenceSweptAreaSolid
    from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

    p0 = tuple(float(c) for c in path_pts[0])
    x, y, n = _profile_frame(profile_normal, ydir=profile_ydir, xdir=profile_xdir)
    pts3d = [Point(*p) for p in _embed_profile_3d(profile2d, p0, x, y)]

    # Closed profile outer curve (line edges) in absolute 3D.
    ring = pts3d + [pts3d[0]]
    outer_curve = IndexedPolyCurve([Edge(ring[i], ring[i + 1]) for i in range(len(ring) - 1)])
    profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

    # Directrix: polyline spine through the path points (absolute 3D).
    dpts = [Point(*[float(c) for c in p]) for p in path_pts]
    directrix = IndexedPolyCurve([Edge(dpts[i], dpts[i + 1]) for i in range(len(dpts) - 1)])

    # Identity position — profile and directrix already carry absolute coordinates.
    position = Axis2Placement3D(Point(0.0, 0.0, 0.0))
    solid = FixedReferenceSweptAreaSolid(profile, position, directrix)
    return active_backend().build(Geometry("sweep_ref", solid))


def build_three_sweeps():
    """Create three swept solids from the input arrays with consistent profile alignment."""
    # According to tests, use Z-up as profile Y direction (i.e., local +Y along global Z)
    profile_y = (0.0, 0.0, 1.0)

    # Normals for each sweep so that the 2D profile faces a consistent side
    sweep1_normal = (0.0, 1.0, 0.0)  # along +Y
    sweep2_normal = (-1.0, 0.0, 0.0)  # along -X
    sweep3_normal = (1.0, 0.0, 0.0)  # along +X

    sh1 = sweep_profile_along_path(sweep1_pts, fillet, sweep1_normal, profile_ydir=profile_y)
    sh2 = sweep_profile_along_path(sweep2_pts, fillet, sweep2_normal, profile_ydir=profile_y)
    sh3 = sweep_profile_along_path(sweep3_pts, fillet, sweep3_normal, profile_ydir=profile_y)
    return sh1, sh2, sh3


def get_three_sweeps_mesh_data():
    """Export per-shape geometry data (triangulated) for the three sweeps.

    Returns: List of dicts with keys: vertices (Nx3 float list), faces (Mx3 int list).
    """
    from ada.occ.tessellating import shape_to_tri_mesh

    shapes_ = build_three_sweeps()
    data = []
    for sh in shapes_:
        tm = shape_to_tri_mesh(sh)
        # Ensure pure Python lists for stable JSON-like comparison (avoid np types)
        verts = tm.vertices.tolist()
        faces = tm.faces.tolist()
        data.append({"vertices": verts, "faces": faces})
    return data


def adapy_viewer(shapes_):
    import trimesh

    from ada.comms.fb.fb_scene_gen import SceneDC, SceneOperationsDC
    from ada.occ.tessellating import shape_to_tri_mesh
    from ada.visit.render_params import RenderParams
    from ada.visit.renderer_manager import RendererManager

    scene = trimesh.Scene()
    for shp in shapes_:
        scene.add_geometry(shape_to_tri_mesh(shp))

    source_params = RenderParams(stream_from_ifc_store=False, scene=SceneDC(operation=SceneOperationsDC.ADD))

    renderer_manager = RendererManager("react")
    renderer_manager.render(scene, source_params)


if __name__ == "__main__":
    try:
        shapes = build_three_sweeps()
    except Exception as e:
        print("Failed to build sweeps:", e)
    else:
        print("Successfully built 3 swept solids. Launching viewer...")
        adapy_viewer(shapes)
