"""Streaming AP242 / AP214 STEP (ISO-10303-21) writer.

Kernel-free: this module imports no CAD kernel (no OpenCASCADE, no adacpp). It
authors the STEP B-rep entities directly from adapy's parametric geometry
(``ExtrudedAreaSolid``) and streams them one object at a time, so peak memory is
O(number of objects) rather than O(total geometry).

This exists because the OCC ``STEPCAFControl_Writer`` path accumulates every
solid into a ``TopoDS_Compound`` + an XCAF document and then builds a second
full in-memory copy (the AP242 entity graph) during ``Transfer`` before writing
-- the peak holds both at once and OOM-kills the viewer worker on large FEM
models. See ``Part.to_stp(..., writer="stream")``.

Geometry coverage: plates, straight beams, and straight pipe segments -- anything
whose ``solid_geom()`` is a plain ``ExtrudedAreaSolid``. Straight segments emit
``PLANE`` faces; circular arcs / circles (tubular sections) emit true
``CYLINDRICAL_SURFACE`` faces with seam handling. Hollow sections become inner
bounds on the end caps. Tapered/swept/revolved members and curved plates are
skipped (counted in the returned stats).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Geometry IR
# --------------------------------------------------------------------------- #
@dataclass
class Seg:
    """One boundary segment in 2D profile coordinates."""

    kind: str  # "line" | "arc"
    start: tuple
    end: tuple
    mid: tuple | None = None  # on-curve midpoint, required for kind == "arc"


@dataclass
class Extrusion:
    origin: tuple  # profile-plane origin in world coords
    xdir: tuple  # profile local +x in world coords (unit)
    normal: tuple  # extrude axis = profile +z in world coords (unit)
    depth: float  # extrude distance along +normal
    outer: list  # list[Seg], closed, CCW about +normal
    inners: list = field(default_factory=list)  # list[list[Seg]], closed, CW about +normal
    name: str = "obj"
    color: tuple | None = None  # rgb in 0..1, or None


# --------------------------------------------------------------------------- #
# Tiny vector helpers (plain tuples, no numpy)
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n == 0.0:
        raise ValueError("cannot normalize a zero-length vector")
    return (a[0] / n, a[1] / n, a[2] / n)


# --------------------------------------------------------------------------- #
# 2D arc helpers (profile plane)
# --------------------------------------------------------------------------- #
def _arc_center_2d(a, m, b):
    """Circumcenter of the three 2D points a, m, b."""
    ax, ay = a
    mx, my = m
    bx, by = b
    d = 2.0 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(d) < 1e-18:
        raise ValueError("degenerate arc: start/mid/end are collinear")
    a2 = ax * ax + ay * ay
    m2 = mx * mx + my * my
    b2 = bx * bx + by * by
    ux = (a2 * (my - by) + m2 * (by - ay) + b2 * (ay - my)) / d
    uy = (a2 * (bx - mx) + m2 * (ax - bx) + b2 * (mx - ax)) / d
    return (ux, uy)


def _arc_is_ccw(a, m, b, center):
    """True if travelling a -> m -> b sweeps CCW about +z (in profile 2D)."""
    cx, cy = center
    ang_a = math.atan2(a[1] - cy, a[0] - cx)
    ang_m = math.atan2(m[1] - cy, m[0] - cx)
    ang_b = math.atan2(b[1] - cy, b[0] - cx)
    two_pi = 2.0 * math.pi
    dm = (ang_m - ang_a) % two_pi
    db = (ang_b - ang_a) % two_pi
    return 0.0 < dm < db


# --------------------------------------------------------------------------- #
# Schema presets
# --------------------------------------------------------------------------- #
_SCHEMAS = {
    "AP242": {
        "file_schema": "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }",
        "app_context": "managed model based 3d engineering",
        "protocol": "ap242_managed_model_based_3d_engineering_mim_lf",
        "year": 2014,
    },
    "AP214": {
        "file_schema": "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }",
        "app_context": "core data for automotive mechanical design processes",
        "protocol": "automotive_design",
        "year": 2010,
    },
}


# --------------------------------------------------------------------------- #
# Streaming writer
# --------------------------------------------------------------------------- #
class Ap242StreamWriter:
    """Stream extrusions to an open text file handle as STEP Part-21."""

    def __init__(
        self,
        fh,
        *,
        schema="AP242",
        length_unit="METRE",
        length_prefix=None,
        product_name="model",
        uncertainty=1e-6,
        timestamp="1970-01-01T00:00:00",
        context_style="standard",
        assembly=True,
    ):
        schema = schema.upper()
        if schema not in _SCHEMAS:
            raise ValueError(f"unknown schema {schema!r}; expected one of {list(_SCHEMAS)}")
        if context_style not in ("standard", "simple"):
            raise ValueError("context_style must be 'standard' or 'simple'")
        self.fh = fh
        self.schema = schema
        self.length_unit = length_unit
        self.length_prefix = length_prefix  # None or e.g. "MILLI"
        self.product_name = product_name
        self.uncertainty = uncertainty
        self.timestamp = timestamp
        self.context_style = context_style
        # assembly=True emits one named component PRODUCT per member under a root
        # assembly PRODUCT (flat NEXT_ASSEMBLY_USAGE_OCCURRENCE tree), so member
        # names show up in CAD assembly trees -- parity with the OCC XCAF writer.
        # assembly=False emits all solids into one anonymous representation.
        self.assembly = assembly
        self._id = 0
        self._geom_ctx = None
        self._ident_axis = None  # shared identity AXIS2_PLACEMENT_3D (lazy)
        self._solids = []  # used when assembly is False
        self._styled = []
        self._components = []  # (product_definition_id, shape_rep_id, name) when assembly
        self._began = False
        self._ended = False

    # -- low level ---------------------------------------------------------- #
    def _w(self, body):
        self._id += 1
        self.fh.write(f"#{self._id}={body};\n")
        return self._id

    @staticmethod
    def _r(x):
        """Format a float as a STEP real (always carries a decimal point)."""
        s = "%.12g" % float(x)
        if "e" in s or "E" in s:
            mant, _, exp = s.replace("E", "e").partition("e")
            if "." not in mant:
                mant += "."
            return mant + "E" + exp
        if "." not in s:
            s += "."
        return s

    def _p3(self, p):
        return f"({self._r(p[0])},{self._r(p[1])},{self._r(p[2])})"

    @staticmethod
    def _refs(ids):
        return "(" + ",".join(f"#{i}" for i in ids) + ")"

    # -- geometry primitives ------------------------------------------------ #
    def _pt(self, p):
        return self._w(f"CARTESIAN_POINT('',{self._p3(p)})")

    def _dir(self, v):
        return self._w(f"DIRECTION('',{self._p3(v)})")

    def _vertex(self, pt_id):
        return self._w(f"VERTEX_POINT('',#{pt_id})")

    def _line_edge(self, v0, p0, v1, p1):
        d = _unit(_sub(p1, p0))
        dir_id = self._dir(d)
        vec_id = self._w(f"VECTOR('',#{dir_id},1.)")
        p0_id = self._pt(p0)
        line_id = self._w(f"LINE('',#{p0_id},#{vec_id})")
        return self._w(f"EDGE_CURVE('',#{v0},#{v1},#{line_id},.T.)")

    def _arc_edge(self, v0, p0, v1, p1, center3, ref3, radius, same_sense, axis3):
        loc_id = self._pt(center3)
        axis_id = self._dir(axis3)
        ref_id = self._dir(ref3)
        a2p = self._w(f"AXIS2_PLACEMENT_3D('',#{loc_id},#{axis_id},#{ref_id})")
        circ_id = self._w(f"CIRCLE('',#{a2p},{self._r(radius)})")
        flag = ".T." if same_sense else ".F."
        return self._w(f"EDGE_CURVE('',#{v0},#{v1},#{circ_id},{flag})")

    def _oriented(self, edge_id, sense):
        flag = ".T." if sense else ".F."
        return self._w(f"ORIENTED_EDGE('',*,*,#{edge_id},{flag})")

    def _edge_loop(self, oriented_ids):
        return self._w(f"EDGE_LOOP('',{self._refs(oriented_ids)})")

    def _plane(self, loc, axis, ref):
        loc_id = self._pt(loc)
        axis_id = self._dir(axis)
        ref_id = self._dir(ref)
        a2p = self._w(f"AXIS2_PLACEMENT_3D('',#{loc_id},#{axis_id},#{ref_id})")
        return self._w(f"PLANE('',#{a2p})")

    def _cylinder(self, center, axis, ref, radius):
        loc_id = self._pt(center)
        axis_id = self._dir(axis)
        ref_id = self._dir(ref)
        a2p = self._w(f"AXIS2_PLACEMENT_3D('',#{loc_id},#{axis_id},#{ref_id})")
        return self._w(f"CYLINDRICAL_SURFACE('',#{a2p},{self._r(radius)})")

    def _axis2(self, center, axis, ref):
        return self._w(f"AXIS2_PLACEMENT_3D('',#{self._pt(center)},#{self._dir(axis)},#{self._dir(ref)})")

    def _conical(self, center, axis, ref, radius, semi_angle):
        a2p = self._axis2(center, axis, ref)
        return self._w(f"CONICAL_SURFACE('',#{a2p},{self._r(radius)},{self._r(semi_angle)})")

    def _spherical(self, center, axis, ref, radius):
        a2p = self._axis2(center, axis, ref)
        return self._w(f"SPHERICAL_SURFACE('',#{a2p},{self._r(radius)})")

    def _toroidal(self, center, axis, ref, major_radius, minor_radius):
        a2p = self._axis2(center, axis, ref)
        return self._w(f"TOROIDAL_SURFACE('',#{a2p},{self._r(major_radius)},{self._r(minor_radius)})")

    def _ellipse_edge(self, v0, v1, center3, ref3, axis3, semi1, semi2, same_sense):
        a2p = self._axis2(center3, axis3, ref3)
        ell = self._w(f"ELLIPSE('',#{a2p},{self._r(semi1)},{self._r(semi2)})")
        flag = ".T." if same_sense else ".F."
        return self._w(f"EDGE_CURVE('',#{v0},#{v1},#{ell},{flag})")

    # -- lifecycle ---------------------------------------------------------- #
    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.end()
        return False

    def begin(self):
        if self._began:
            raise RuntimeError("begin() called twice")
        self._began = True
        sch = _SCHEMAS[self.schema]
        fh = self.fh
        fh.write("ISO-10303-21;\n")
        fh.write("HEADER;\n")
        fh.write("FILE_DESCRIPTION(('streaming FEM plate/beam export'),'2;1');\n")
        fh.write("FILE_NAME('%s','%s',(''),(''),'ap242_stream','adapy','');\n" % (self.product_name, self.timestamp))
        fh.write("FILE_SCHEMA(('%s'));\n" % sch["file_schema"])
        fh.write("ENDSEC;\n")
        fh.write("DATA;\n")

        app = self._w(f"APPLICATION_CONTEXT('{sch['app_context']}')")
        self._w(
            "APPLICATION_PROTOCOL_DEFINITION('international standard'," f"'{sch['protocol']}',{sch['year']},#{app})"
        )
        self._prod_ctx = self._w(f"PRODUCT_CONTEXT('',#{app},'mechanical')")
        self._pd_ctx = self._w(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app},'design')")

        if self.context_style == "simple":
            self._geom_ctx = self._w("GEOMETRIC_REPRESENTATION_CONTEXT('','3D',3)")
            return

        prefix = "." + self.length_prefix + "." if self.length_prefix else "$"
        len_unit = self._w(f"(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT({prefix},.{self.length_unit}.))")
        ang_unit = self._w("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
        sol_unit = self._w("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
        unc = self._w(
            f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE({self._r(self.uncertainty)}),"
            f"#{len_unit},'distance_accuracy_value','edge curve and vertex accuracy')"
        )
        self._geom_ctx = self._w(
            "(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
            f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc}))"
            f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{len_unit},#{ang_unit},#{sol_unit}))"
            "REPRESENTATION_CONTEXT('Context','3D'))"
        )

    def end(self):
        if not self._began:
            raise RuntimeError("end() called before begin()")
        if self._ended:
            return
        self._ended = True

        if self.assembly:
            self._emit_assembly_root()
        else:
            self._emit_single_rep()

        if self._styled:
            self._w(
                "MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',"
                f"{self._refs(self._styled)},#{self._geom_ctx})"
            )

        self.fh.write("ENDSEC;\n")
        self.fh.write("END-ISO-10303-21;\n")

    def _emit_single_rep(self):
        """All solids in one anonymous representation (assembly=False)."""
        axis = self._identity_axis()
        items = [axis, *self._solids]
        rep = self._w(
            f"ADVANCED_BREP_SHAPE_REPRESENTATION('{self.product_name}'," f"{self._refs(items)},#{self._geom_ctx})"
        )
        product = self._w(f"PRODUCT('{self.product_name}','{self.product_name}','',(#{self._prod_ctx}))")
        self._w(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{product}))")
        pdf = self._w(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
        pd = self._w(f"PRODUCT_DEFINITION('design','',#{pdf},#{self._pd_ctx})")
        pds = self._w(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
        self._w(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")

    def _emit_assembly_root(self):
        """Root assembly PRODUCT + a flat NAUO link to each named component."""
        axis = self._identity_axis()
        root_sr = self._w(f"SHAPE_REPRESENTATION('{self.product_name}',(#{axis}),#{self._geom_ctx})")
        product = self._w(f"PRODUCT('{self.product_name}','{self.product_name}','',(#{self._prod_ctx}))")
        self._w(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{product}))")
        pdf = self._w(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
        root_pd = self._w(f"PRODUCT_DEFINITION('design','',#{pdf},#{self._pd_ctx})")
        root_pds = self._w(f"PRODUCT_DEFINITION_SHAPE('','',#{root_pd})")
        self._w(f"SHAPE_DEFINITION_REPRESENTATION(#{root_pds},#{root_sr})")

        for idx, (comp_pd, comp_sr, name) in enumerate(self._components, start=1):
            nauo = self._w(f"NEXT_ASSEMBLY_USAGE_OCCURRENCE('{idx}','{name}','',#{root_pd},#{comp_pd},$)")
            nauo_pds = self._w(f"PRODUCT_DEFINITION_SHAPE('','',#{nauo})")
            # Identity transform: the component geometry is already in world coords.
            idt = self._w(f"ITEM_DEFINED_TRANSFORMATION('','',#{axis},#{axis})")
            rrwt = self._w(
                f"(REPRESENTATION_RELATIONSHIP('','',#{comp_sr},#{root_sr})"
                f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{idt})"
                "SHAPE_REPRESENTATION_RELATIONSHIP())"
            )
            self._w(f"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{rrwt},#{nauo_pds})")

    # -- the main entry point ----------------------------------------------- #
    def add_extrusion(self, ext: Extrusion) -> int:
        if not self._began or self._ended:
            raise RuntimeError("add_extrusion() must be called between begin() and end()")

        normal = _unit(ext.normal)
        xdir = _unit(ext.xdir)
        ydir = _unit(_cross(normal, xdir))
        origin = ext.origin
        depth_vec = _scale(normal, ext.depth)

        def to3d_base(p2):
            return _add(origin, _add(_scale(xdir, p2[0]), _scale(ydir, p2[1])))

        def to3d_top(p2):
            return _add(to3d_base(p2), depth_vec)

        all_faces = []
        loops_built = []

        loops = [(ext.outer, True)] + [(inner, False) for inner in ext.inners]
        for segs, is_outer in loops:
            built = self._build_loop(segs, is_outer, to3d_base, to3d_top, normal, xdir)
            all_faces.extend(built["side_faces"])
            loops_built.append(built)

        top_loc = loops_built[0]["bpos_top"][0]
        bot_loc = loops_built[0]["bpos_base"][0]
        all_faces.append(self._cap(loops_built, top=True, loc=top_loc, normal=normal, ref=xdir))
        all_faces.append(self._cap(loops_built, top=False, loc=bot_loc, normal=normal, ref=xdir))

        shell = self._w(f"CLOSED_SHELL('',{self._refs(all_faces)})")
        name = ext.name.replace("'", "''")
        brep = self._w(f"MANIFOLD_SOLID_BREP('{name}',#{shell})")

        if ext.color is not None:
            self._emit_color(brep, ext.color)

        if self.assembly:
            self._emit_component(brep, name)
        else:
            self._solids.append(brep)
        return brep

    def _emit_component(self, brep_id, name):
        """Wrap one solid as a named component PRODUCT with its own shape rep.

        Records (product_definition, shape_representation, name) so ``end()`` can
        link it under the root assembly via NEXT_ASSEMBLY_USAGE_OCCURRENCE.
        """
        axis = self._identity_axis()
        sr = self._w(f"ADVANCED_BREP_SHAPE_REPRESENTATION('{name}',(#{axis},#{brep_id}),#{self._geom_ctx})")
        product = self._w(f"PRODUCT('{name}','{name}','',(#{self._prod_ctx}))")
        self._w(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{product}))")
        pdf = self._w(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
        pd = self._w(f"PRODUCT_DEFINITION('design','',#{pdf},#{self._pd_ctx})")
        pds = self._w(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
        self._w(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{sr})")
        self._components.append((pd, sr, name))

    def _identity_axis(self):
        if self._ident_axis is None:
            o = self._pt((0.0, 0.0, 0.0))
            z = self._dir((0.0, 0.0, 1.0))
            x = self._dir((1.0, 0.0, 0.0))
            self._ident_axis = self._w(f"AXIS2_PLACEMENT_3D('',#{o},#{z},#{x})")
        return self._ident_axis

    # -- direct B-rep emission (imported shapes / pure shells / reader output) --- #
    def add_brep(self, g, *, name="shape", color=None, translate=(0.0, 0.0, 0.0)):
        """Emit an arbitrary adapy B-rep geometry — ClosedShell / OpenShell /
        ShellBasedSurfaceModel / AdvancedFace — as STEP. The inverse of the streaming
        reader; covers imported shapes and thickness-less shells. Returns the top item
        id, or None if any face uses a surface/curve not yet emitted kernel-free (the
        shape is skipped wholesale, never partially emitted)."""
        import ada.geom.surfaces as su

        self._t = (float(translate[0]), float(translate[1]), float(translate[2]))
        self._vcache: dict = {}  # coord -> VERTEX_POINT id (shared across all faces)
        self._ecache: dict = {}  # topological-edge key -> (EDGE_CURVE id, v0, v1) for edge sharing
        nm = (name or "shape").replace("'", "''")

        if isinstance(g, su.ClosedShell):
            faces = self._brep_faces(g.cfs_faces)
            if faces is None:
                return None
            shell = self._w(f"CLOSED_SHELL('',{self._refs(faces)})")
            item = self._w(f"MANIFOLD_SOLID_BREP('{nm}',#{shell})")
        elif isinstance(g, su.OpenShell):
            faces = self._brep_faces(g.cfs_faces)
            if faces is None:
                return None
            shell = self._w(f"OPEN_SHELL('',{self._refs(faces)})")
            item = self._w(f"SHELL_BASED_SURFACE_MODEL('{nm}',(#{shell}))")
        elif isinstance(g, su.ShellBasedSurfaceModel):
            shell_ids = []
            for sh in g.sbsm_boundary:
                faces = self._brep_faces(sh.cfs_faces)
                if faces is None:
                    return None
                kw = "CLOSED_SHELL" if isinstance(sh, su.ClosedShell) else "OPEN_SHELL"
                shell_ids.append(self._w(f"{kw}('',{self._refs(faces)})"))
            if not shell_ids:
                return None
            item = self._w(f"SHELL_BASED_SURFACE_MODEL('{nm}',{self._refs(shell_ids)})")
        elif isinstance(g, su.AdvancedFace):
            faces = self._brep_faces([g])
            if faces is None:
                return None
            shell = self._w(f"OPEN_SHELL('',{self._refs(faces)})")
            item = self._w(f"SHELL_BASED_SURFACE_MODEL('{nm}',(#{shell}))")
        else:
            return None

        if color is not None:
            self._emit_color(item, color)
        if self.assembly:
            self._emit_component(item, nm)
        else:
            self._solids.append(item)
        return item

    def _brep_faces(self, faces):
        out = []
        for f in faces:
            fid = self._brep_face(f)
            if fid is None:
                return None  # unsupported face -> abort the whole shape
            out.append(fid)
        return out or None

    def _brep_face(self, face):
        import ada.geom.surfaces as su

        if not isinstance(face, su.AdvancedFace):
            return None
        surf = self._brep_surface(face.face_surface)
        if surf is None:
            return None
        bounds = []
        for i, fb in enumerate(face.bounds):
            loop = self._brep_loop(fb.bound)
            if loop is None:
                return None
            kw = "FACE_OUTER_BOUND" if i == 0 else "FACE_BOUND"
            bounds.append(self._w(f"{kw}('',#{loop},{'.T.' if fb.orientation else '.F.'})"))
        if not bounds:
            return None
        return self._w(f"ADVANCED_FACE('',{self._refs(bounds)},#{surf},{'.T.' if face.same_sense else '.F.'})")

    def _brep_surface(self, s):
        import ada.geom.surfaces as su

        p = getattr(s, "position", None)
        if p is None:
            return None
        loc = self._tp(p.location)
        axis = _axis_or(p.axis, (0, 0, 1))
        ref = _axis_or(p.ref_direction, (1, 0, 0))
        if isinstance(s, su.Plane):
            return self._plane(loc, axis, ref)
        if isinstance(s, su.CylindricalSurface):
            return self._cylinder(loc, axis, ref, s.radius)
        if isinstance(s, su.ConicalSurface):
            return self._conical(loc, axis, ref, s.radius, s.semi_angle)
        if isinstance(s, su.SphericalSurface):
            return self._spherical(loc, axis, ref, s.radius)
        if isinstance(s, su.ToroidalSurface):
            return self._toroidal(loc, axis, ref, s.major_radius, s.minor_radius)
        return None  # B-spline / profile surfaces not yet emitted kernel-free

    def _brep_loop(self, loop):
        import ada.geom.curves as cu

        if isinstance(loop, cu.EdgeLoop):
            oriented = []
            for oe in loop.edge_list:
                oid = self._brep_oriented(oe)
                if oid is None:
                    return None
                oriented.append(oid)
            return self._edge_loop(oriented) if oriented else None
        if isinstance(loop, cu.PolyLoop):
            pts = loop.polygon
            if len(pts) < 2:
                return None
            oriented = []
            n = len(pts)
            for i in range(n):
                a, b = pts[i], pts[(i + 1) % n]
                va, vb = self._vfor(a), self._vfor(b)
                pa, pb = self._tp(a), self._tp(b)
                # PolyLoop has no EdgeCurve objects to key on; a straight line between
                # two vertices is unique, so a geometric vertex-pair key shares it
                # safely with an adjacent face (no arc-collision concern for lines).
                key = ("L", min(va, vb), max(va, vb))
                oriented.append(
                    self._shared_oriented(
                        key, va, vb, va, pa, vb, pb, lambda v0, p0, v1, p1: self._line_edge(v0, p0, v1, p1)
                    )
                )
            return self._edge_loop(oriented)
        return None

    def _brep_oriented(self, oe):
        """Emit one ORIENTED_EDGE, sharing the underlying EDGE_CURVE with any
        adjacent face that already emitted the same topological edge — what makes
        the shell a watertight solid OCC accepts (each edge bounds exactly 2 faces).
        Returns the ORIENTED_EDGE id, or None for an unsupported edge geometry."""
        import ada.geom.curves as cu

        ec = getattr(oe, "edge_element", oe)
        if not isinstance(ec, cu.EdgeCurve):
            return None
        g = ec.edge_geometry
        # The EDGE_CURVE is emitted once in ec.start->ec.end direction; the per-face
        # ORIENTED_EDGE flag is computed from the loop's traversal (oe.start->oe.end).
        if isinstance(g, cu.Line):

            def emit(v0, p0, v1, p1):
                return self._line_edge(v0, p0, v1, p1)

        elif isinstance(g, cu.Circle):
            pos = g.position

            def emit(v0, p0, v1, p1):
                return self._arc_edge(
                    v0,
                    p0,
                    v1,
                    p1,
                    self._tp(pos.location),
                    _axis_or(pos.ref_direction, (1, 0, 0)),
                    g.radius,
                    ec.same_sense,
                    _axis_or(pos.axis, (0, 0, 1)),
                )

        elif isinstance(g, cu.Ellipse):
            pos = g.position

            def emit(v0, p0, v1, p1):
                return self._ellipse_edge(
                    v0,
                    v1,
                    self._tp(pos.location),
                    _axis_or(pos.ref_direction, (1, 0, 0)),
                    _axis_or(pos.axis, (0, 0, 1)),
                    g.semi_axis1,
                    g.semi_axis2,
                    ec.same_sense,
                )

        else:
            return None  # B-spline edge -> unsupported

        # Key by the EdgeCurve OBJECT identity: a truly-shared edge resolves to the
        # same ec object in both adjacent faces (reader memoisation), while the two
        # semicircle arcs of one circle are distinct objects — so they are NOT merged
        # (a geometric vertex-pair key collides them and corrupts the topology).
        return self._shared_oriented(
            id(ec),
            self._vfor(oe.start),
            self._vfor(oe.end),  # loop traversal direction
            self._vfor(ec.start),
            self._tp(ec.start),
            self._vfor(ec.end),
            self._tp(ec.end),  # EDGE_CURVE emit dir
            emit,
        )

    def _shared_oriented(self, key, t0, t1, e0, pe0, e1, pe1, emit):
        """Reuse (or emit once) the EDGE_CURVE identified by ``key`` and wrap it in an
        ORIENTED_EDGE. ``(t0, t1)`` is how THIS loop walks the edge; the flag is .T.
        iff that matches the EDGE_CURVE's emitted direction. ``emit(v0,p0,v1,p1)``
        writes a fresh EDGE_CURVE in e0->e1 direction."""
        cached = self._ecache.get(key)
        if cached is None:
            edge_id = emit(e0, pe0, e1, pe1)
            self._ecache[key] = (edge_id, e0, e1)
            ev0, ev1 = e0, e1
        else:
            edge_id, ev0, ev1 = cached
        return self._oriented(edge_id, t0 == ev0 and t1 == ev1)

    def _vfor(self, p):
        tp = self._tp(p)
        key = (round(tp[0], 9), round(tp[1], 9), round(tp[2], 9))
        vid = self._vcache.get(key)
        if vid is None:
            vid = self._vertex(self._pt(tp))
            self._vcache[key] = vid
        return vid

    def _tp(self, p):
        t = getattr(self, "_t", (0.0, 0.0, 0.0))
        return (float(p[0]) + t[0], float(p[1]) + t[1], float(p[2]) + t[2])

    # -- loop / face construction ------------------------------------------- #
    def _build_loop(self, segs, is_outer, to3d_base, to3d_top, normal, xdir):
        n = len(segs)
        verts2d = [s.start for s in segs]
        bpos_base = [to3d_base(p) for p in verts2d]
        bpos_top = [to3d_top(p) for p in verts2d]
        bpid = [self._pt(p) for p in bpos_base]
        tpid = [self._pt(p) for p in bpos_top]
        bv = [self._vertex(i) for i in bpid]
        tv = [self._vertex(i) for i in tpid]

        eb = [None] * n
        et = [None] * n
        ve = [None] * n
        for i in range(n):
            ve[i] = self._line_edge(bv[i], bpos_base[i], tv[i], bpos_top[i])

        side_faces = []
        for i, seg in enumerate(segs):
            j = (i + 1) % n
            if seg.kind == "line":
                eb[i] = self._line_edge(bv[i], bpos_base[i], bv[j], bpos_base[j])
                et[i] = self._line_edge(tv[i], bpos_top[i], tv[j], bpos_top[j])
                tangent = _unit(_sub(bpos_base[j], bpos_base[i]))
                out_n = _unit(_cross(tangent, normal))
                surf = self._plane(bpos_base[i], out_n, tangent)
                face_sense = True
            elif seg.kind == "arc":
                c2 = _arc_center_2d(seg.start, seg.mid, seg.end)
                radius = math.hypot(seg.start[0] - c2[0], seg.start[1] - c2[1])
                ccw = _arc_is_ccw(seg.start, seg.mid, seg.end, c2)
                c3_base = to3d_base(c2)
                c3_top = to3d_top(c2)
                eb[i] = self._arc_edge(bv[i], bpos_base[i], bv[j], bpos_base[j], c3_base, xdir, radius, ccw, normal)
                et[i] = self._arc_edge(tv[i], bpos_top[i], tv[j], bpos_top[j], c3_top, xdir, radius, ccw, normal)
                surf = self._cylinder(c3_base, normal, xdir, radius)
                face_sense = is_outer
            else:
                raise ValueError(f"unknown segment kind {seg.kind!r}")

            loop = self._edge_loop(
                [
                    self._oriented(eb[i], True),
                    self._oriented(ve[j], True),
                    self._oriented(et[i], False),
                    self._oriented(ve[i], False),
                ]
            )
            bound = self._w(f"FACE_OUTER_BOUND('',#{loop},.T.)")
            flag = ".T." if face_sense else ".F."
            side_faces.append(self._w(f"ADVANCED_FACE('',(#{bound}),#{surf},{flag})"))

        return {
            "is_outer": is_outer,
            "eb": eb,
            "et": et,
            "bpos_base": bpos_base,
            "bpos_top": bpos_top,
            "side_faces": side_faces,
        }

    def _cap(self, loops_built, *, top, loc, normal, ref):
        axis = normal if top else _scale(normal, -1.0)
        surf = self._plane(loc, axis, ref)

        bounds = []
        for built in loops_built:
            edges = built["et"] if top else built["eb"]
            n = len(edges)
            if top:
                oriented = [self._oriented(edges[i], True) for i in range(n)]
            else:
                oriented = [self._oriented(edges[i], False) for i in range(n - 1, -1, -1)]
            loop = self._edge_loop(oriented)
            if built["is_outer"]:
                bounds.insert(0, self._w(f"FACE_OUTER_BOUND('',#{loop},.T.)"))
            else:
                bounds.append(self._w(f"FACE_BOUND('',#{loop},.T.)"))

        return self._w(f"ADVANCED_FACE('',{self._refs(bounds)},#{surf},.T.)")

    # -- styling ------------------------------------------------------------ #
    def _emit_color(self, brep_id, rgb):
        r, g, b = rgb
        col = self._w(f"COLOUR_RGB('',{self._r(r)},{self._r(g)},{self._r(b)})")
        fac = self._w(f"FILL_AREA_STYLE_COLOUR('',#{col})")
        fas = self._w(f"FILL_AREA_STYLE('',(#{fac}))")
        ssfa = self._w(f"SURFACE_STYLE_FILL_AREA(#{fas})")
        sss = self._w(f"SURFACE_SIDE_STYLE('',(#{ssfa}))")
        ssu = self._w(f"SURFACE_STYLE_USAGE(.BOTH.,#{sss})")
        psa = self._w(f"PRESENTATION_STYLE_ASSIGNMENT((#{ssu}))")
        si = self._w(f"STYLED_ITEM('color',(#{psa}),#{brep_id})")
        self._styled.append(si)


# --------------------------------------------------------------------------- #
# Loop builders (also usable directly without adapy geometry)
# --------------------------------------------------------------------------- #
def polygon_loop(points2d):
    n = len(points2d)
    return [Seg("line", points2d[i], points2d[(i + 1) % n]) for i in range(n)]


def circle_loop(center, radius, *, ccw=True):
    cx, cy = center
    right = (cx + radius, cy)
    left = (cx - radius, cy)
    top = (cx, cy + radius)
    bot = (cx, cy - radius)
    if ccw:
        return [Seg("arc", right, left, mid=top), Seg("arc", left, right, mid=bot)]
    return [Seg("arc", right, left, mid=bot), Seg("arc", left, right, mid=top)]


# --------------------------------------------------------------------------- #
# adapy geometry -> Extrusion IR
# --------------------------------------------------------------------------- #
def _xy(p):
    return (float(p[0]), float(p[1]))


def _signed_area(segs):
    a = 0.0
    for s in segs:
        a += s.start[0] * s.end[1] - s.end[0] * s.start[1]
    return 0.5 * a


def _reverse(segs):
    return [Seg(s.kind, s.end, s.start, s.mid) for s in reversed(segs)]


def _orient(segs, *, ccw):
    if (_signed_area(segs) > 0.0) != ccw:
        return _reverse(segs)
    return segs


def _curve_to_segs(curve, *, is_outer):
    """Translate one adapy 2D profile curve into a closed list[Seg], or None."""
    from ada.geom.curves import ArcLine, Circle, Edge, IndexedPolyCurve

    if isinstance(curve, Circle):
        return circle_loop(_xy(curve.position.location), float(curve.radius), ccw=is_outer)

    if isinstance(curve, IndexedPolyCurve):
        segs = []
        for seg in curve.segments:
            if isinstance(seg, Edge):
                segs.append(Seg("line", _xy(seg.start), _xy(seg.end)))
            elif isinstance(seg, ArcLine):
                segs.append(Seg("arc", _xy(seg.start), _xy(seg.end), mid=_xy(seg.midpoint)))
            else:
                logger.warning("unhandled poly-curve segment %s", type(seg).__name__)
                return None
        return _orient(segs, ccw=is_outer)

    logger.warning("unhandled profile curve type %s", type(curve).__name__)
    return None


def extrusion_from_geometry(geom, *, name="obj", color=None, translate=(0.0, 0.0, 0.0)):
    """Build an :class:`Extrusion` from an adapy ``Geometry`` whose solid is a
    plain ``ExtrudedAreaSolid``. Returns None if the geometry is not a supported
    extrusion (tapered/swept/revolved, curved plate, etc.).

    ``translate`` is added to the placement origin -- used to apply the parent
    part's absolute location, matching the OCC ``to_stp`` path.
    """
    from ada.geom.solids import ExtrudedAreaSolid, ExtrudedAreaSolidTapered

    solid = getattr(geom, "geometry", None)
    if solid is None:
        solid = getattr(geom, "solid", None)
    if not isinstance(solid, ExtrudedAreaSolid) or isinstance(solid, ExtrudedAreaSolidTapered):
        return None

    if float(solid.extruded_direction[2]) <= 0.0:
        logger.warning("%s extrudes along non-positive local z; skipping", name)
        return None

    pos = solid.position
    origin = (
        float(pos.location[0]) + translate[0],
        float(pos.location[1]) + translate[1],
        float(pos.location[2]) + translate[2],
    )
    xdir = tuple(float(v) for v in pos.ref_direction)
    normal = tuple(float(v) for v in pos.axis)

    profile = solid.swept_area
    outer = _curve_to_segs(profile.outer_curve, is_outer=True)
    if outer is None:
        return None
    inners = []
    for inner in getattr(profile, "inner_curves", []) or []:
        loop = _curve_to_segs(inner, is_outer=False)
        if loop is not None:
            inners.append(loop)

    return Extrusion(
        origin=origin,
        xdir=xdir,
        normal=normal,
        depth=float(solid.depth),
        outer=outer,
        inners=inners,
        name=name or "obj",
        color=color,
    )


def _primitive_to_extrusion(geom, *, name="obj", color=None, translate=(0.0, 0.0, 0.0)):
    """Box / Cylinder primitives ARE extrusions (a rectangle / circle swept along
    the local axis by z_length / height); route them through the proven extrusion
    path so they emit as watertight solids. Returns None for other primitives
    (Cone is tapered, Sphere periodic — handled elsewhere / not yet)."""
    from ada.geom.solids import Box, Cylinder

    solid = getattr(geom, "geometry", None)
    if isinstance(solid, Box):
        x, y = float(solid.x_length), float(solid.y_length)
        outer = [
            Seg("line", (0.0, 0.0), (x, 0.0)),
            Seg("line", (x, 0.0), (x, y)),
            Seg("line", (x, y), (0.0, y)),
            Seg("line", (0.0, y), (0.0, 0.0)),
        ]
        depth = float(solid.z_length)
    elif isinstance(solid, Cylinder):
        outer = circle_loop((0.0, 0.0), float(solid.radius), ccw=True)
        depth = float(solid.height)
    else:
        return None

    pos = solid.position
    origin = (
        float(pos.location[0]) + translate[0],
        float(pos.location[1]) + translate[1],
        float(pos.location[2]) + translate[2],
    )
    return Extrusion(
        origin=origin,
        xdir=tuple(float(v) for v in (pos.ref_direction if pos.ref_direction is not None else (1.0, 0.0, 0.0))),
        normal=tuple(float(v) for v in (pos.axis if pos.axis is not None else (0.0, 0.0, 1.0))),
        depth=depth,
        outer=outer,
        inners=[],
        name=name or "obj",
        color=color,
    )


def _units_to_step(part):
    units = str(getattr(part, "units", "m")).lower()
    if units in ("mm", "millimetre", "millimeter") or units.endswith("mm"):
        return "METRE", "MILLI"
    return "METRE", None


def write_step_stream(
    part,
    destination_file,
    *,
    schema="AP242",
    assembly=True,
    progress_callback=None,
):
    """Stream every supported physical object under ``part`` to a STEP file.

    Returns a stats dict: ``{"emitted": int, "skipped": int}``. Kernel-free --
    builds no OCC/adacpp shapes, so it avoids the memory spike that OOMs the OCC
    ``STEPCAFControl_Writer`` path on large FEM models.
    """
    objects = list(part.get_all_physical_objects(pipe_to_segments=True))
    total = len(objects)
    lu, lp = _units_to_step(part)
    emitted = 0
    skipped = 0

    with open(destination_file, "w") as fh:
        writer = Ap242StreamWriter(
            fh,
            schema=schema,
            length_unit=lu,
            length_prefix=lp,
            product_name=part.name or "model",
            assembly=assembly,
        )
        writer.begin()
        for i, obj in enumerate(objects, start=1):
            geom, name, color, translate = _object_geom_meta(obj)
            done = False
            if geom is not None:
                ext = extrusion_from_geometry(
                    geom, name=name, color=color, translate=translate
                ) or _primitive_to_extrusion(geom, name=name, color=color, translate=translate)
                if ext is not None:
                    writer.add_extrusion(ext)
                    done = True
                elif writer.add_brep(geom.geometry, name=name, color=color, translate=translate) is not None:
                    # B-rep fallback: imported shapes, pure shells, analytic faces
                    done = True
            emitted += 1 if done else 0
            skipped += 0 if done else 1
            if progress_callback is not None:
                progress_callback(i, total)
        writer.end()

    logger.info("write_step_stream: emitted %d, skipped %d of %d", emitted, skipped, total)
    return {"emitted": emitted, "skipped": skipped}


def _axis_or(d, default):
    """A direction as a 3-list, or ``default`` when the optional axis is unset."""
    return list(d) if d is not None else list(default)


def _object_geom_meta(obj):
    """(geom, name, color, translate) for a physical object, or (None, ...) if it
    has no usable solid geometry. Shared by the extrusion and B-rep emit paths."""
    none = (None, None, None, (0.0, 0.0, 0.0))
    solid_geom = getattr(obj, "solid_geom", None)
    if solid_geom is None:
        return none
    try:
        geom = solid_geom()
    except Exception as exc:  # degenerate geometry, unsupported type, etc.
        logger.warning("solid_geom() failed for %s (%s); skipping", getattr(obj, "name", "?"), exc)
        return none

    translate = (0.0, 0.0, 0.0)
    parent = getattr(obj, "parent", None)
    if parent is not None and getattr(parent, "placement", None) is not None:
        try:
            loc = parent.placement.to_axis2placement3d(use_absolute_placement=True).location
            translate = (float(loc[0]), float(loc[1]), float(loc[2]))
        except Exception:
            translate = (0.0, 0.0, 0.0)

    color = None
    obj_color = getattr(obj, "color", None)
    if obj_color is not None and getattr(obj_color, "rgb", None) is not None:
        try:
            color = tuple(float(c) for c in obj_color.rgb)
        except Exception:
            color = None

    return geom, getattr(obj, "name", "obj"), color, translate


def _extrusion_from_object(obj):
    """Best-effort Extrusion for a single physical object, or None."""
    geom, name, color, translate = _object_geom_meta(obj)
    if geom is None:
        return None
    return extrusion_from_geometry(geom, name=name, color=color, translate=translate)
