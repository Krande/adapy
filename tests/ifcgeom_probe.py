"""Run ifcopenshell geometry OUT OF PROCESS, so a broken build is a test result, not a dead run.

The conda-forge ifcopenshell 0.9.0 macOS build links its libraries ``-flat_namespace -undefined
suppress``: a symbol the loader cannot resolve is not a link error but a ``dyld`` abort the first
time the geometry mapping touches it -- i.e. constructing ``ifcopenshell.geom.iterator``. An abort
cannot be caught; in-process it kills pytest at ~2% and hides the state of everything after it.

This module is the shared machinery for the two places that care:

* ``tests/core/cadit/ifc/test_ifcopenshell_geometry_health.py`` -- the regression tests that say,
  loudly and per schema / representation / operation, whether geometry works.
* ``tests/conftest.py`` -- a once-per-session probe; when it finds a crashing build, the
  geometry-dependent tests SKIP (naming the probe result) instead of taking the process down.

Model builders here use plain ``ifcopenshell.file.create_entity`` -- WRITING an IFC never enters
the geometry libraries, so it is safe in the parent. Only reading geometry goes to the child.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.guid

SCHEMAS = ("IFC2X3", "IFC4", "IFC4X3_ADD2")
OPS = ("iterator", "create_shape")

#: Marks the child's single line of machine-readable output among whatever else it prints.
_RESULT_TAG = "IFCGEOM_RESULT "

_CHILD = textwrap.dedent(
    """
    import faulthandler, json, sys
    faulthandler.enable()
    import ifcopenshell, ifcopenshell.geom

    path, ops = sys.argv[1], sys.argv[2].split(",")

    def summary(geometry):
        v = list(geometry.verts)
        xs, ys, zs = v[0::3], v[1::3], v[2::3]
        bbox = [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]] if v else None
        return {"verts": len(v) // 3, "faces": len(geometry.faces) // 3, "bbox": bbox}

    f = ifcopenshell.open(path)
    out = {"schema": f.schema_identifier, "version": ifcopenshell.version}
    for op in ops:
        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        # Progress goes to stderr so a native abort leaves a trail of how far it got.
        print(f"[{op}] start", file=sys.stderr, flush=True)
        shapes = {}
        if op == "iterator":
            it = ifcopenshell.geom.iterator(settings, f)
            print(f"[{op}] constructed", file=sys.stderr, flush=True)
            if it.initialize():
                while True:
                    s = it.get()
                    shapes[s.guid] = summary(s.geometry)
                    if not it.next():
                        break
        elif op == "create_shape":
            for p in f.by_type("IfcProduct"):
                if p.Representation is not None:
                    shapes[p.GlobalId] = summary(ifcopenshell.geom.create_shape(settings, p).geometry)
        else:
            raise SystemExit(f"unknown op {op!r}")
        print(f"[{op}] done: {len(shapes)} shapes", file=sys.stderr, flush=True)
        out[op] = shapes
    print(%r + json.dumps(out), flush=True)
    """
    % _RESULT_TAG
)


@dataclass
class GeomRun:
    """What one child process did with one IFC file."""

    path: str
    ops: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    result: dict | None = field(default=None)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.result is not None

    def describe(self) -> str:
        """``rc`` (+ signal name) and the stderr tail: everything needed to act on a crash."""
        rc = self.returncode
        sig = ""
        if rc < 0:
            try:
                sig = f" ({signal.Signals(-rc).name})"
            except ValueError:
                pass
        # A native crash dumps the C stack innermost-first, and its bottom half is always the same
        # CPython eval loop; drop those frames so the tail shows where it actually died.
        lines = [ln for ln in self.stderr.strip().splitlines() if not _INTERPRETER_FRAME.search(ln)]
        tail = "\n".join(lines[-25:])
        return f"ops={','.join(self.ops)} returncode={rc}{sig}\n--- child stderr (tail) ---\n{tail}"


_INTERPRETER_FRAME = re.compile(r'Binary file "[^"]*(/bin/python[\d.]*|/usr/lib/dyld|[\\/]python[\d.]*\.exe)"')


def run_geometry(path: str | os.PathLike, ops=OPS, timeout: float = 180) -> GeomRun:
    """Read ``path``'s geometry with ``ops`` in a fresh interpreter; never raises on a crash."""
    ops = tuple(ops)
    try:
        p = subprocess.run(
            [sys.executable, "-c", _CHILD, str(path), ",".join(ops)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = -999, e.stdout or "", f"{e.stderr or ''}\nTIMEOUT after {timeout}s"
        out, err = (x.decode(errors="replace") if isinstance(x, bytes) else x for x in (out, err))
    result = None
    for line in out.splitlines():
        if line.startswith(_RESULT_TAG):
            result = json.loads(line[len(_RESULT_TAG) :])
    return GeomRun(str(path), ops, rc, out, err, result)


# --- model builders -----------------------------------------------------------------------------


def _new_file(schema: str) -> tuple[ifcopenshell.file, object]:
    """An IFC file with a project, metre units and a 3D Body context; returns ``(file, context)``."""
    f = ifcopenshell.file(schema=schema)
    origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None)
    ctx = f.createIfcGeometricRepresentationContext(None, "Model", 3, 1.0e-5, origin, None)
    metre = f.createIfcSIUnit(None, "LENGTHUNIT", None, "METRE")
    f.createIfcProject(
        ifcopenshell.guid.new(), None, "health", None, None, None, None, [ctx], f.createIfcUnitAssignment([metre])
    )
    return f, ctx


def _add_product(f, ctx, name: str, rep_type: str, item) -> None:
    rep = f.createIfcShapeRepresentation(ctx, "Body", rep_type, [item])
    shape = f.createIfcProductDefinitionShape(None, None, [rep])
    placement = f.createIfcLocalPlacement(
        None, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None)
    )
    f.createIfcBuildingElementProxy(ifcopenshell.guid.new(), None, name, None, None, placement, shape, None)


def _extrusion(f):
    """1 x 0.5 rectangle extruded 2 along +Z -> bbox (0,0,0)-(1,0.5,2)."""
    pos2d = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.5, 0.25)), None)
    profile = f.createIfcRectangleProfileDef("AREA", None, pos2d, 1.0, 0.5)
    pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None)
    return f.createIfcExtrudedAreaSolid(profile, pos, f.createIfcDirection((0.0, 0.0, 1.0)), 2.0)


_TETRA = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
_TETRA_FACES = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)]  # outward, 0-based


def _faceted_brep(f):
    """Unit tetrahedron as an IfcFacetedBrep (the mesh body every schema, incl. IFC2X3, has)."""
    pts = [f.createIfcCartesianPoint(p) for p in _TETRA]
    faces = [
        f.createIfcFace([f.createIfcFaceOuterBound(f.createIfcPolyLoop([pts[i] for i in tri]), True)])
        for tri in _TETRA_FACES
    ]
    return f.createIfcFacetedBrep(f.createIfcClosedShell(faces))


def _triangulated(f):
    """Unit tetrahedron as an IfcTriangulatedFaceSet (IFC4+)."""
    coords = f.createIfcCartesianPointList3D(_TETRA)
    index = [tuple(i + 1 for i in tri) for tri in _TETRA_FACES]
    return f.create_entity("IfcTriangulatedFaceSet", Coordinates=coords, Closed=True, CoordIndex=index)


#: body name -> (IFC RepresentationType, builder, expected world bbox)
BODIES = {
    "extrusion": ("SweptSolid", _extrusion, ((0, 0, 0), (1, 0.5, 2))),
    "faceted_brep": ("Brep", _faceted_brep, ((0, 0, 0), (1, 1, 1))),
    "triangulated_face_set": ("Tessellation", _triangulated, ((0, 0, 0), (1, 1, 1))),
}


def write_body(schema: str, body: str, path: str | os.PathLike) -> tuple[tuple, tuple]:
    """Write a one-product IFC with ``body``; returns the expected world bbox."""
    rep_type, build, bbox = BODIES[body]
    f, ctx = _new_file(schema)
    _add_product(f, ctx, body, rep_type, build(f))
    f.write(str(path))
    return bbox


# --- the session probe ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """Per-schema outcome of the minimal extrusion run through every geometry op."""

    runs: dict[str, GeomRun]

    def failure(self, schema: str | None = None) -> str | None:
        """Why geometry on ``schema`` is broken, or None. ``None`` schema -> any schema broken."""
        schemas = [s for s in self.runs if schema is None or s.upper() == schema.upper()]
        if schema is not None and not schemas:
            # A schema the probe did not cover (e.g. IFC4X1): judge it by the rest.
            schemas = list(self.runs)
        bad = [s for s in schemas if not self.runs[s].ok]
        if not bad:
            return None
        return "; ".join(f"{s}: rc={self.runs[s].returncode}" for s in bad)

    @property
    def healthy(self) -> bool:
        return self.failure() is None

    def report(self) -> str:
        lines = [f"ifcopenshell {ifcopenshell.version} geometry probe ({sys.executable}):"]
        for s, r in self.runs.items():
            lines.append(f"  {s}: {'ok' if r.ok else 'BROKEN ' + r.describe()}")
        return "\n".join(lines)


def probe(workdir: str | os.PathLike) -> ProbeResult:
    """Run the extrusion case per schema in parallel child processes (~one ifcopenshell import)."""
    workdir = pathlib.Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for schema in SCHEMAS:
        paths[schema] = workdir / f"probe_{schema}.ifc"
        write_body(schema, "extrusion", paths[schema])
    with ThreadPoolExecutor(len(SCHEMAS)) as pool:
        runs = dict(zip(SCHEMAS, pool.map(lambda s: run_geometry(paths[s]), SCHEMAS)))
    return ProbeResult(runs)
