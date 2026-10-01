"""Is the installed ifcopenshell's GEOMETRY kernel actually usable? Checked out of process.

Regression guard for the conda-forge ifcopenshell 0.9.0 macOS build, whose libraries are linked
``-flat_namespace -undefined suppress``: ``ifcopenshell.geom.iterator`` died in ``dyld`` on a
missing symbol, which killed the whole pytest run at ~2% and hid everything behind it.

Every schema x representation x operation below runs in its own child interpreter
(:mod:`tests.ifcgeom_probe`), so a native crash is reported here as a FAILURE naming the schema,
the body, the operation, the return code / signal and the child's stderr tail -- and the rest of
the suite keeps running (``tests/conftest.py`` skips the geometry-dependent tests on such a build).
The checks are on real output (vertex/face counts, world bounding box), not just "did not crash".

``test_macos_dylibs_two_level_namespace`` guards the root cause statically.
"""

from __future__ import annotations

import json
import pathlib
import platform
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.ifcgeom_probe import OPS, SCHEMAS, run_geometry, write_body

T = 0.025
#: The thickened arch plate (see ``_write_advanced_brep``): bottom patch apex at z=0.15.
_ARCH_BBOX = ((0.0, 0.0, 0.0), (1.0, 1.0, 0.15 + T))


def _cases():
    for schema in SCHEMAS:
        bodies = ["extrusion", "faceted_brep"]
        if schema != "IFC2X3":  # IfcTriangulatedFaceSet / IfcAdvancedBrep are IFC4+
            bodies += ["triangulated_face_set", "advanced_brep"]
        for body in bodies:
            yield schema, body


CASES = list(_cases())


def _write_advanced_brep(schema: str, path: pathlib.Path) -> tuple:
    """adapy's own thickened curved-plate export: an IfcAdvancedBrep of B-spline patches.

    Built through ``Assembly.to_ifc`` so this is exactly the body the curved-plate export ships
    (``tests/core/api/plates/test_plate_curved_thick.py`` is where the native crash surfaced).
    """
    import ada
    import ada.geom.curves as cu
    import ada.geom.surfaces as su
    from ada.geom import Geometry
    from ada.geom.curves import KnotType
    from ada.geom.direction import Direction
    from ada.geom.points import Point

    surf = su.BSplineSurfaceWithKnots(
        u_degree=2,
        v_degree=1,
        control_points_list=[
            [Point(0, 0, 0), Point(0, 1, 0)],
            [Point(0.5, 0, 0.3), Point(0.5, 1, 0.3)],
            [Point(1, 0, 0), Point(1, 1, 0)],
        ],
        surface_form=su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=[3, 3],
        v_multiplicities=[2, 2],
        u_knots=[0.0, 1.0],
        v_knots=[0.0, 1.0],
        knot_spec=KnotType.UNSPECIFIED,
    )

    def spline(y: float) -> cu.BSplineCurveWithKnots:
        return cu.BSplineCurveWithKnots(
            degree=2,
            control_points_list=[Point(0, y, 0), Point(0.5, y, 0.3), Point(1, y, 0)],
            curve_form=cu.BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=False,
            self_intersect=False,
            knot_multiplicities=[3, 3],
            knots=[0.0, 1.0],
            knot_spec=KnotType.UNSPECIFIED,
        )

    p00, p10, p11, p01 = Point(0, 0, 0), Point(1, 0, 0), Point(1, 1, 0), Point(0, 1, 0)
    e0 = cu.EdgeCurve(p00, p10, edge_geometry=spline(0.0), same_sense=True)
    e1 = cu.EdgeCurve(p10, p11, edge_geometry=cu.Line(p10, Direction(0, 1, 0)), same_sense=True)
    e2 = cu.EdgeCurve(p01, p11, edge_geometry=spline(1.0), same_sense=True)
    e3 = cu.EdgeCurve(p01, p00, edge_geometry=cu.Line(p01, Direction(0, -1, 0)), same_sense=True)
    loop = cu.EdgeLoop(
        edge_list=[
            cu.OrientedEdge(p00, p10, edge_element=e0, orientation=True),
            cu.OrientedEdge(p10, p11, edge_element=e1, orientation=True),
            cu.OrientedEdge(p11, p01, edge_element=e2, orientation=False),
            cu.OrientedEdge(p01, p00, edge_element=e3, orientation=True),
        ]
    )
    face = su.AdvancedFace(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=True)
    plate = ada.PlateCurved("curved1", Geometry("arch", face, None), t=T)
    a = ada.Assembly("A", schema=schema) / (ada.Part("P") / plate)
    a.to_ifc(str(path), validate=False)

    import ifcopenshell

    f = ifcopenshell.open(str(path))
    assert f.schema_identifier.upper() == schema.upper()
    assert len(f.by_type("IfcAdvancedBrep")) == 1, "the curved plate should export as one IfcAdvancedBrep"
    return _ARCH_BBOX


def _write_case(schema: str, body: str, path: pathlib.Path) -> tuple:
    if body == "advanced_brep":
        return _write_advanced_brep(schema, path)
    return write_body(schema, body, path)


@pytest.fixture(scope="module")
def geometry_runs(tmp_path_factory):
    """Every (schema, body, op) child run, done concurrently once; each test asserts its own."""
    d = tmp_path_factory.mktemp("ifcgeom_health")
    expected, jobs = {}, []
    for schema, body in CASES:
        path = d / f"{schema}_{body}.ifc"
        expected[(schema, body)] = _write_case(schema, body, path)
        jobs += [(schema, body, op, path) for op in OPS]
    with ThreadPoolExecutor(8) as pool:
        runs = pool.map(lambda j: (j[:3], run_geometry(j[3], ops=(j[2],))), jobs)
        return expected, dict(runs)


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("schema, body", CASES, ids=[f"{s}-{b}" for s, b in CASES])
def test_geometry_kernel(geometry_runs, schema, body, op):
    expected, runs = geometry_runs
    run = runs[(schema, body, op)]
    if not run.ok:
        pytest.fail(f"ifcopenshell.geom.{op} on a {schema} {body} crashed or errored: {run.describe()}", False)

    shapes = run.result[op]
    assert len(shapes) == 1, f"{schema} {body} via {op}: expected 1 shape, got {len(shapes)}"
    (shape,) = shapes.values()
    assert shape["verts"] >= 4 and shape["faces"] >= 4, f"{schema} {body} via {op}: degenerate mesh {shape}"

    (lo, hi), got = expected[(schema, body)], shape["bbox"]
    # Planar bodies tessellate exactly; the B-spline arch is sampled, so its apex lands within
    # the mesher's deflection of the true value.
    tol = 5e-3 if body == "advanced_brep" else 1e-6
    assert got[0] == pytest.approx(list(lo), abs=tol), f"{schema} {body} via {op}: bbox min {got[0]}"
    assert got[1] == pytest.approx(list(hi), abs=tol), f"{schema} {body} via {op}: bbox max {got[1]}"


def test_both_ops_agree(geometry_runs):
    """iterator and create_shape are two routes into one mapping; they must produce the same mesh."""
    _, runs = geometry_runs
    for schema, body in CASES:
        a, b = runs[(schema, body, "iterator")], runs[(schema, body, "create_shape")]
        if not (a.ok and b.ok):
            continue  # reported by test_geometry_kernel
        sa, sb = next(iter(a.result["iterator"].values())), next(iter(b.result["create_shape"].values()))
        assert sa["verts"] == sb["verts"] and sa["faces"] == sb["faces"], (schema, body, sa, sb)
        assert sum(sa["bbox"], []) == pytest.approx(sum(sb["bbox"], []), abs=1e-9), (schema, body)


# --- macOS: the root cause, checked statically --------------------------------------------------

#: conda-forge ifcopenshell builds whose macOS libraries are KNOWN to be flat-namespace /
#: undefined-suppress linked: ``(version, build_number)``. The fixed build bumps the build
#: number, so installing it turns the xfail below into an XPASS -- which ``strict=True`` makes a
#: FAILURE: the reminder to delete the entry. Do not add builds here to make CI green.
_KNOWN_FLAT_NAMESPACE_BUILDS = {("0.9.0", 0)}
_FEEDSTOCK_ISSUE = (
    "conda-forge ifcopenshell 0.9.0 (build 0) macOS libs are linked -flat_namespace -undefined suppress; "
    "a missing symbol aborts in dyld at runtime (ifcopenshell.geom.iterator). "
    "Fix belongs in https://github.com/conda-forge/ifcopenshell-feedstock"
)


def _conda_record() -> dict | None:
    """The conda-meta record ifcopenshell was installed from, if it was installed by conda."""
    import ifcopenshell

    imported = pathlib.Path(ifcopenshell.__file__).resolve()
    for p in (pathlib.Path(sys.prefix) / "conda-meta").glob("ifcopenshell-*.json"):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("name") != "ifcopenshell":
            continue
        # Only trust it if the imported package IS the one conda installed (not a pip overlay).
        inits = (pathlib.Path(sys.prefix, f).resolve() for f in rec.get("files", []) if f.endswith("__init__.py"))
        if imported in inits:
            return rec
    return None


def _macho_binaries() -> list[tuple[pathlib.Path, bool]]:
    """``(path, is_python_extension)`` for every Mach-O binary the ifcopenshell install ships."""
    import ifcopenshell

    rec = _conda_record()
    if rec is not None:
        paths = [pathlib.Path(sys.prefix, f) for f in rec["files"] if f.endswith((".dylib", ".so"))]
    else:  # a wheel: the extension + delocated dylibs live inside the package
        root = pathlib.Path(ifcopenshell.__file__).resolve().parent
        paths = [p for p in root.rglob("*") if p.suffix in (".dylib", ".so")]
    # The CPython extension module legitimately leaves Python's C-API symbols undefined (they are
    # resolved from the host interpreter, `-undefined dynamic_lookup`); plain shared libraries
    # must resolve everything at link time. The record lists the versioned symlinks too
    # (libifcopenshell.parse.0.9.dylib -> ...0.9.0.dylib): inspect each real file once.
    unique = {p.resolve(): p for p in paths if p.is_file()}
    return [(p, p.suffix == ".so" and "site-packages" in p.parts) for p in unique.values()]


def _xfail_known_broken_build():
    rec = _conda_record()
    if rec is None:
        return []
    key = (rec.get("version"), int(rec.get("build_number", -1)))
    if key in _KNOWN_FLAT_NAMESPACE_BUILDS:
        return [pytest.mark.xfail(strict=True, reason=f"{_FEEDSTOCK_ISSUE} [installed: {rec.get('build')}]")]
    return []


@pytest.mark.skipif(platform.system() != "Darwin", reason="Mach-O link flags: macOS only")
def test_macos_dylibs_two_level_namespace(request):
    for mark in _xfail_known_broken_build():
        request.applymarker(mark)

    bins = _macho_binaries()
    assert bins, "found no ifcopenshell Mach-O binaries to inspect"
    problems = []
    for path, is_ext in bins:
        header = subprocess.run(["otool", "-hv", str(path)], capture_output=True, text=True, check=True).stdout
        # `otool -hv` prints the flag names without their MH_ prefix ("NOUNDEFS DYLDLINK TWOLEVEL ...").
        flags = {tok.removeprefix("MH_") for tok in header.split()}
        missing = [f"MH_{f}" for f in ("TWOLEVEL", "NOUNDEFS") if f not in flags and not (is_ext and f == "NOUNDEFS")]
        if missing:
            problems.append(f"{path.name}: lacks {', '.join(missing)}")
    assert not problems, f"{len(problems)} of {len(bins)} ifcopenshell binaries mislinked:\n" + "\n".join(problems)
