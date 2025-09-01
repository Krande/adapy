# Sweep example
# Build swept solids from point arrays using pythonocc-core, with consistent profile alignment.
# Run this file directly to visualize the 3 sweeps if you have a GUI environment.
from typing import Iterable, List, Optional, Tuple

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_RoundCorner

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
# OCC-based sweep construction
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


def make_wire_from_points(pts: List[Iterable[float]]):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCC.Core.gp import gp_Pnt

    assert len(pts) >= 2, "Need at least two points for a wire"
    mk_wire = BRepBuilderAPI_MakeWire()
    for i in range(len(pts) - 1):
        p1 = gp_Pnt(*pts[i])
        p2 = gp_Pnt(*pts[i + 1])
        mk_wire.Add(BRepBuilderAPI_MakeEdge(p1, p2).Edge())
    return mk_wire.Wire()


def make_profile_wire(
    poly2d: List[Tuple[float, float]],
    origin: Tuple[float, float, float],
    normal: Tuple[float, float, float],
    ydir: Optional[Tuple[float, float, float]] = None,
    xdir: Optional[Tuple[float, float, float]] = None,
):
    """
    Build a 3D profile wire from 2D points by embedding them in a plane at origin with
    the provided normal and an orientation controlled by ydir/xdir.

    - If xdir is provided, it is used as the X of the local frame.
    - Else if ydir is provided, X is computed as cross(ydir, normal).
    - Y is recomputed as cross(normal, X) to ensure orthonormality.
    """
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCC.Core.gp import gp_Pnt

    n = _normalize(normal)
    if xdir is not None:
        x = _normalize(xdir)
        # Ensure x is not parallel to n
        if abs(_dot(x, n)) > 0.999:
            raise ValueError("profile_xdir nearly parallel to profile_normal")
        y = _cross(n, x)
    else:
        if ydir is None:
            # Default up direction if nothing provided
            ydir = (0.0, 0.0, 1.0)
        y0 = _normalize(ydir)
        # Make sure y0 is not parallel to normal
        if abs(_dot(y0, n)) > 0.999:
            # pick another helper
            y0 = (1.0, 0.0, 0.0)
        x = _normalize(_cross(y0, n))
        y = _normalize(_cross(n, x))

    ox, oy, oz = origin

    mk = BRepBuilderAPI_MakePolygon()
    for i, (u, v) in enumerate(poly2d + [poly2d[0]]):  # ensure closed
        px = ox + u * x[0] + v * y[0]
        py = oy + u * x[1] + v * y[1]
        pz = oz + u * x[2] + v * y[2]
        mk.Add(gp_Pnt(px, py, pz))
    mk.Close()
    return mk.Wire(), x, y, n


def sweep_profile_along_path(
    path_pts: List[Iterable[float]],
    profile2d: List[Tuple[float, float]],
    profile_normal: Tuple[float, float, float],
    profile_ydir: Optional[Tuple[float, float, float]] = None,
    profile_xdir: Optional[Tuple[float, float, float]] = None,
):
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell

    # Build a smooth C1 spine wire for consistent section transport
    # spine = make_bspline_wire_from_points(path_pts)
    spine = make_wire_from_points(path_pts)

    # Define frame at first point using provided profile orientation
    p0 = tuple(path_pts[0])
    profile_wire, x, y, n = make_profile_wire(profile2d, p0, profile_normal, ydir=profile_ydir, xdir=profile_xdir)

    mkpipe = BRepOffsetAPI_MakePipeShell(spine)
    mkpipe.SetTransitionMode(BRepBuilderAPI_RoundCorner)
    # mkpipe.SetMode(ax2)  # fixed reference trihedron, avoids flipping w.r.t different path directions
    # Add the profile with contact, without correction, to keep a constant cross-section
    mkpipe.Add(profile_wire, True, False)
    mkpipe.Build()

    # Create a solid (caps are created by MakeSolid if profile is a face)
    if not mkpipe.IsDone():
        raise RuntimeError("PipeShell build failed")
    mkpipe.MakeSolid()
    return mkpipe.Shape()


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


def _try_display(shapes):
    try:
        from OCC.Display.SimpleGui import init_display
    except Exception:
        print("pythonocc-core viewer not available; skipping display.")
        return

    display, start_display, add_menu, add_function_to_menu = init_display()
    for sh in shapes:
        display.DisplayShape(sh, update=False)
    display.FitAll()
    start_display()


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
        print("Successfully built 3 swept solids. Launching viewer (if available)...")
        _try_display(shapes)
        # adapy_viewer(shapes)
