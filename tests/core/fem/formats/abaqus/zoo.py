"""A model zoo for the Abaqus writer: small, deterministic models that together reach every module
under ``ada.fem.formats.abaqus.write``.

Two jobs:

* a byte-for-byte baseline -- write every model, change the writer, write again, diff;
* the input for read -> write -> read round-trip tests.

So every builder is deterministic: fixed names, element/node/mass ids and coordinates, no meshing
and no randomness. Each one is small and aimed at one writer area; ``ZOO`` maps a name to its
builder. Builders only use adapy's public FEM API, the way a user would.
"""

from __future__ import annotations

import pathlib
from typing import Callable

import numpy as np

import ada
from ada import Node
from ada.base.types import GeomRepr
from ada.fem import (
    Amplitude,
    Bc,
    Connector,
    ConnectorSection,
    Constraint,
    Csys,
    Elem,
    FemSection,
    FemSet,
    FieldOutput,
    HistOutput,
    Interaction,
    InteractionProperty,
    Load,
    LoadGravity,
    LoadPoint,
    LoadPressure,
    Mass,
    PredefinedField,
    Spring,
    StepEigen,
    StepExplicit,
    StepImplicitDynamic,
    StepImplicitStatic,
    StepSteadyState,
    Surface,
)
from ada.fem.formats.abaqus.solver import Stabilize, StabilizeTypes
from ada.fem.interactions import ContactTypes
from ada.fem.steps import StepEigenComplex, StepSolverOptions
from ada.materials.metals import CarbonSteel, DnvGl16Mat

REPO_FILES = pathlib.Path(__file__).resolve().parents[5] / "files"


# ── building blocks ──────────────────────────────────────────────────────────────────────────


def _model(part_name: str = "P1", mat_name: str = "S355", **mat_kwargs) -> tuple[ada.Assembly, ada.Part, ada.Material]:
    """An assembly with one part and one material, both named."""
    a = ada.Assembly("Zoo")
    p = ada.Part(part_name)
    a.add_part(p)
    mat = p.add_material(ada.Material(mat_name, CarbonSteel("S355", **mat_kwargs)))
    return a, p, mat


def _nodes(fem: ada.FEM, coords, start: int = 1) -> list[Node]:
    out = []
    for i, c in enumerate(coords, start):
        out.append(fem.nodes.add(Node(c, i, parent=fem)))
    return [fem.nodes.from_id(i) for i in range(start, start + len(coords))]


def _elems(fem: ada.FEM, el_type: str, connectivity, start: int = 1) -> list[Elem]:
    n = fem.nodes.from_id
    els = [Elem(start + i, [n(j) for j in conn], el_type, parent=fem) for i, conn in enumerate(connectivity)]
    for e in els:
        fem.add_elem(e)
    return els


def _elset(fem: ada.FEM, name: str, members) -> FemSet:
    return fem.add_set(FemSet(name, list(members), "elset", parent=fem))


def _nset(fem: ada.FEM, name: str, members) -> FemSet:
    return fem.add_set(FemSet(name, list(members), "nset", parent=fem))


def _plate(fem: ada.FEM, mat, *, node_start=1, el_start=1, z=0.0, name="plate", el_type="QUAD", thickness=0.01):
    """A 2x1 quad (or 4-tri) plate at height ``z`` with a shell section."""
    coords = [(0, 0, z), (1, 0, z), (2, 0, z), (0, 1, z), (1, 1, z), (2, 1, z)]
    ids = [node_start + i for i in range(6)]
    _nodes(fem, coords, node_start)
    a, b, c, d, e, f = ids
    if el_type == "QUAD":
        conn = [(a, b, e, d), (b, c, f, e)]
    else:
        conn = [(a, b, e), (a, e, d), (b, c, f), (b, f, e)]
    els = _elems(fem, el_type, conn, el_start)
    es = _elset(fem, name, els)
    fem.add_section(FemSection(f"sec_{name}", GeomRepr.SHELL, es, mat, thickness=thickness, parent=fem))
    return els, es


def _unit_hex(fem: ada.FEM, mat, *, node_start=1, el_start=1, x0=0.0, name="block"):
    """One C3D8 unit cube with a solid section."""
    coords = [
        (x0, 0, 0),
        (x0 + 1, 0, 0),
        (x0 + 1, 1, 0),
        (x0, 1, 0),
        (x0, 0, 1),
        (x0 + 1, 0, 1),
        (x0 + 1, 1, 1),
        (x0, 1, 1),
    ]
    _nodes(fem, coords, node_start)
    els = _elems(fem, "HEXAHEDRON", [tuple(range(node_start, node_start + 8))], el_start)
    es = _elset(fem, name, els)
    fem.add_section(FemSection(f"sec_{name}", GeomRepr.SOLID, es, mat, parent=fem))
    return els, es


def _line(fem: ada.FEM, mat, profile: str, *, node_start: int, el_start: int, y: float, name: str, el_type="LINE"):
    """A 2-element beam along x at height ``y`` with a beam section of ``profile``."""
    if el_type == "LINE":
        _nodes(fem, [(0, y, 0), (1, y, 0), (2, y, 0)], node_start)
        conn = [(node_start, node_start + 1), (node_start + 1, node_start + 2)]
    else:  # LINE3: end, end, mid (adapy's order)
        _nodes(fem, [(0, y, 0), (2, y, 0), (1, y, 0)], node_start)
        conn = [(node_start, node_start + 1, node_start + 2)]
    els = _elems(fem, el_type, conn, el_start)
    es = _elset(fem, name, els)
    sec = ada.Section(f"prof_{name}", from_str=profile)
    fem.add_section(
        FemSection(f"sec_{name}", GeomRepr.LINE, es, mat, section=sec, local_z=(0, 0, 1), local_y=(0, 1, 0), parent=fem)
    )
    return els, es


def _static_step(a: ada.Assembly, name: str = "static", **kwargs) -> StepImplicitStatic:
    return a.fem.add_step(StepImplicitStatic(name, total_time=1.0, init_incr=0.1, max_incr=0.5, **kwargs))


# ── nodes, elements, sections ────────────────────────────────────────────────────────────────


def elements_shell() -> ada.Assembly:
    """S4 and S3 shells, with shell sections of two thicknesses."""
    a, p, mat = _model()
    _plate(p.fem, mat, name="quads")
    _plate(p.fem, mat, node_start=11, el_start=11, z=1.0, name="tris", el_type="TRIANGLE", thickness=0.02)
    return a


def elements_shell_second_order() -> ada.Assembly:
    """S8R (QUAD8 only writes with reduced integration) and S7 (TRIANGLE7)."""
    a, p, mat = _model()
    fem = p.fem
    fem.options.ABAQUS.default_elements.use_reduced_integration = True
    coords8 = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 0, 0), (1, 0.5, 0), (0.5, 1, 0), (0, 0.5, 0)]
    _nodes(fem, coords8, 1)
    q8 = _elems(fem, "QUAD8", [tuple(range(1, 9))], 1)
    es = _elset(fem, "quad8", q8)
    fem.add_section(FemSection("sec_quad8", GeomRepr.SHELL, es, mat, thickness=0.01, parent=fem))
    return a


def elements_shell_tri7() -> ada.Assembly:
    """S7: the only second-order triangle the writer can emit at full integration."""
    a, p, mat = _model()
    fem = p.fem
    coords7 = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0.5, 0, 0), (0.5, 0.5, 0), (0, 0.5, 0), (1 / 3, 1 / 3, 0)]
    _nodes(fem, coords7, 1)
    t7 = _elems(fem, "TRIANGLE7", [tuple(range(1, 8))], 1)
    es = _elset(fem, "tri7", t7)
    fem.add_section(FemSection("sec_tri7", GeomRepr.SHELL, es, mat, thickness=0.01, parent=fem))
    return a


def elements_shell_tri6() -> ada.Assembly:
    """TRIANGLE6: readable, but the writer refuses it at both full and reduced integration."""
    a, p, mat = _model()
    fem = p.fem
    coords6 = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0.5, 0, 0), (0.5, 0.5, 0), (0, 0.5, 0)]
    _nodes(fem, coords6, 1)
    t6 = _elems(fem, "TRIANGLE6", [tuple(range(1, 7))], 1)
    es = _elset(fem, "tri6", t6)
    fem.add_section(FemSection("sec_tri6", GeomRepr.SHELL, es, mat, thickness=0.01, parent=fem))
    return a


def elements_solid_first_order() -> ada.Assembly:
    """C3D8, C3D4 and C3D5 solids in separate element sets."""
    a, p, mat = _model()
    fem = p.fem
    _unit_hex(fem, mat, name="hex8")
    _nodes(fem, [(2, 0, 0), (3, 0, 0), (2, 1, 0), (2, 0, 1)], 11)
    tets = _elems(fem, "TETRA", [(11, 12, 13, 14)], 11)
    es_t = _elset(fem, "tet4", tets)
    fem.add_section(FemSection("sec_tet4", GeomRepr.SOLID, es_t, mat, parent=fem))
    _nodes(fem, [(4, 0, 0), (5, 0, 0), (5, 1, 0), (4, 1, 0), (4.5, 0.5, 1)], 21)
    pyr = _elems(fem, "PYRAMID5", [(21, 22, 23, 24, 25)], 21)
    es_p = _elset(fem, "pyr5", pyr)
    fem.add_section(FemSection("sec_pyr5", GeomRepr.SOLID, es_p, mat, parent=fem))
    return a


def _mid(fem, i, j):
    pi, pj = fem.nodes.from_id(i), fem.nodes.from_id(j)
    return tuple((np.asarray(pi.p) + np.asarray(pj.p)) / 2)


def elements_solid_second_order() -> ada.Assembly:
    """C3D10 and C3D20, with mid-side nodes at the edge midpoints (Abaqus edge order)."""
    a, p, mat = _model()
    fem = p.fem
    _nodes(fem, [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], 1)
    edges10 = [(1, 2), (2, 3), (3, 1), (1, 4), (2, 4), (3, 4)]
    _nodes(fem, [_mid(fem, i, j) for i, j in edges10], 5)
    t10 = _elems(fem, "TETRA10", [tuple(range(1, 11))], 1)
    es = _elset(fem, "tet10", t10)
    fem.add_section(FemSection("sec_tet10", GeomRepr.SOLID, es, mat, parent=fem))

    base = [(2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0), (2, 0, 1), (3, 0, 1), (3, 1, 1), (2, 1, 1)]
    _nodes(fem, base, 21)
    e20 = [(1, 2), (2, 3), (3, 4), (4, 1), (5, 6), (6, 7), (7, 8), (8, 5), (1, 5), (2, 6), (3, 7), (4, 8)]
    _nodes(fem, [_mid(fem, 20 + i, 20 + j) for i, j in e20], 29)
    h20 = _elems(fem, "HEXAHEDRON20", [tuple(range(21, 41))], 21)
    es20 = _elset(fem, "hex20", h20)
    fem.add_section(FemSection("sec_hex20", GeomRepr.SOLID, es20, mat, parent=fem))
    return a


def elements_solid_wedge() -> ada.Assembly:
    """C3D6 wedge: readable, but the writer has no default type registered under WEDGE."""
    a, p, mat = _model()
    fem = p.fem
    _nodes(fem, [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1)], 1)
    w = _elems(fem, "WEDGE", [tuple(range(1, 7))], 1)
    es = _elset(fem, "wedge", w)
    fem.add_section(FemSection("sec_wedge", GeomRepr.SOLID, es, mat, parent=fem))
    return a


def elements_line_profiles() -> ada.Assembly:
    """B31 beams, one per beam-section branch: I, BOX, PIPE, CIRC, L, RECT, and GENERAL (a
    channel, which Abaqus has no *Beam Section profile for)."""
    a, p, mat = _model()
    profiles = [
        ("ipe", "IPE300"),
        ("box", "BG200x150x6x6"),
        ("pipe", "OD200x10"),
        ("circ", "CIRC100"),
        ("angle", "L100x100x10"),
        ("flat", "FB100x10"),
        ("channel", "UNP200"),
    ]
    for i, (name, profile) in enumerate(profiles):
        _line(p.fem, mat, profile, node_start=10 * i + 1, el_start=10 * i + 1, y=float(i), name=name)
    return a


def elements_line_verbatim() -> ada.Assembly:
    """A beam section carried verbatim, the way the Abaqus reader keeps one it read: the profile
    keyword in ``metadata['section_type']`` and its first data line in ``metadata['line1']``,
    with a temperature parameter.

    The verbatim lines are what the writer emits, so they must describe the same profile as the
    typed section -- as they do on anything the reader produced. (This model used to pair an
    IPE300 with a BOX line, a model that contradicts itself: which profile it "has" depends on who
    asks.)"""
    a, p, mat = _model()
    els, es = _line(p.fem, mat, "IPE300", node_start=1, el_start=1, y=0.0, name="verbatim")
    sec = next(s for s in p.fem.sections if s.name == "sec_verbatim")
    sec.section = ada.Section(
        "prof_verbatim", "BG", h=0.15, w_btn=0.15, w_top=0.15, t_w=0.008, t_ftop=0.008, t_fbtn=0.008
    )
    sec.metadata.update(section_type="BOX", line1="0.15, 0.15, 0.008, 0.008, 0.008, 0.008", temperature="GRADIENTS")
    return a


def elements_line_second_order() -> ada.Assembly:
    """B32: a three-node beam."""
    a, p, mat = _model()
    _line(p.fem, mat, "IPE300", node_start=1, el_start=1, y=0.0, name="b32", el_type="LINE3")
    return a


def elements_line_explicit() -> ada.Assembly:
    """A beam section under an explicit first step gets ROTARY INERTIA=ISOTROPIC."""
    a, p, mat = _model()
    _line(p.fem, mat, "IPE300", node_start=1, el_start=1, y=0.0, name="beam")
    a.fem.add_step(StepExplicit("expl", total_time=0.01))
    return a


# ── sets, materials ──────────────────────────────────────────────────────────────────────────


def sections_zero_thickness() -> ada.Assembly:
    """A shell section of zero thickness: the writer leaves it out (writes no *Shell Section)."""
    a, p, mat = _model()
    _plate(p.fem, mat, thickness=0.0)
    return a


def sets() -> ada.Assembly:
    """Element and node sets at part level (plain, generate, internal-by-name) and at assembly
    level referring into the instance."""
    a, p, mat = _model()
    fem = p.fem
    els, _ = _plate(fem, mat)
    _nset(fem, "corner", [fem.nodes.from_id(1)])
    _nset(fem, "edge", [fem.nodes.from_id(i) for i in (1, 2, 3)])
    _elset(fem, "_hidden", els[:1])
    # *Nset, generate: written from gen_mem -- but only when the set also has members, since the
    # writer loops over members grouped by instance (a generate set with none is dropped).
    fem.add_set(
        FemSet(
            "gen_nodes",
            [fem.nodes.from_id(i) for i in range(1, 7)],
            "nset",
            metadata=dict(generate=True, gen_mem=[1, 6, 1]),
            parent=fem,
        )
    )

    # assembly-level sets pointing at the part's objects
    a.fem.add_set(FemSet("asm_nodes", [fem.nodes.from_id(4), fem.nodes.from_id(5)], "nset", parent=a.fem))
    a.fem.add_set(FemSet("asm_els", [els[1]], "elset", parent=a.fem))
    return a


def sets_empty() -> ada.Assembly:
    """An element set with no members: the Abaqus writer logs it and leaves it out."""
    a, p, mat = _model()
    _plate(p.fem, mat)
    p.fem.add_set(FemSet("empty", [], "elset", parent=p.fem))
    return a


def materials() -> ada.Assembly:
    """Elastic + density + expansion, plus plasticity, Rayleigh damping, *No Compression, a zero
    density (a massless material: no *Density) and a material carried verbatim (``aba_inp``)."""
    a = ada.Assembly("Zoo")
    p = ada.Part("P1")
    a.add_part(p)
    plastic = p.add_material(ada.Material("S420_pl", CarbonSteel("S420", plasticity_model=DnvGl16Mat(15e-3, "S355"))))
    damped = p.add_material(ada.Material("S355_damped", CarbonSteel("S355")))
    damped.model.rayleigh_damping.alpha = 0.1
    damped.model.rayleigh_damping.beta = 0.01
    nocomp = p.add_material(ada.Material("S355_nocomp", CarbonSteel("S355"), metadata=dict(no_compression=True)))
    massless = p.add_material(ada.Material("massless", CarbonSteel("S355", rho=0.0, zeta=0.0)))
    # The verbatim text is what is written; the typed model says the same (see
    # elements_line_verbatim for why a model must not contradict its own verbatim text).
    verbatim = p.add_material(
        ada.Material(
            "verbatim",
            CarbonSteel("S355", E=2.0e11, v=0.29, rho=7800.0, zeta=0.0),
            metadata=dict(aba_inp="*Material, name=verbatim\n*Elastic\n2.0e11, 0.29\n*Density\n7800.,"),
        )
    )
    fem = p.fem
    for i, m in enumerate([plastic, damped, nocomp, massless, verbatim]):
        _plate(fem, m, node_start=10 * i + 1, el_start=10 * i + 1, z=float(i), name=f"plate_{i}")
    return a


# ── masses, springs, connectors ──────────────────────────────────────────────────────────────


def masses() -> ada.Assembly:
    """*Mass (isotropic), *Rotary Inertia and *Nonstructural Mass."""
    a, p, mat = _model()
    fem = p.fem
    els, es = _plate(fem, mat)
    n1 = _nset(fem, "m_iso", [fem.nodes.from_id(3)])
    fem.add_mass(Mass("iso_mass", n1, 10.0, mass_id=101))
    n3 = _nset(fem, "m_rot", [fem.nodes.from_id(4)])
    fem.add_mass(Mass("rot_inertia", n3, [1.0, 1.0, 1.0, 0.0, 0.0, 0.0], mass_type=Mass.TYPES.ROT_INERTIA, mass_id=103))
    fem.add_mass(Mass("nsm", es, 5.0, mass_type=Mass.TYPES.NONSTRU, units="PER AREA", mass_id=104))
    return a


def masses_anisotropic() -> ada.Assembly:
    """*Mass, type=ANISOTROPIC -- which Abaqus has, and the writer cannot emit."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    n2 = _nset(fem, "m_aniso", [fem.nodes.from_id(6)])
    fem.add_mass(Mass("aniso_mass", n2, [1.0, 2.0, 3.0], ptype=Mass.PTYPES.ANISOTROPIC, mass_id=102))
    return a


def springs() -> ada.Assembly:
    """A SPRING1 grounded spring with a diagonal stiffness."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    fs = _nset(fem, "spr_set", [fem.nodes.from_id(3)])
    stiff = np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]).astype(float)
    fem.add_spring(Spring("spr1", 9001, "SPRING1", fem_set=fs, stiff=stiff, parent=fem))
    return a


def springs_coupled() -> ada.Assembly:
    """A grounded spring whose DOFs are coupled, as a Sesam MGSPRNG gives: no SPRING1 form."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    fs = _nset(fem, "sprc_set", [fem.nodes.from_id(3)])
    stiff = np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]).astype(float)
    stiff[0, 4] = stiff[4, 0] = 1234.5678901234567
    stiff[2, 3] = stiff[3, 2] = -0.1
    fem.add_spring(Spring("sprc", 9003, "SPRING1", fem_set=fs, stiff=stiff, parent=fem))
    return a


def springs_two_node() -> ada.Assembly:
    """A SPRING2 (node-to-node) spring."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    fs = _nset(fem, "spr2_set", [fem.nodes.from_id(3), fem.nodes.from_id(6)])
    stiff = np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]).astype(float)
    fem.add_spring(Spring("spr2", 9002, "SPRING2", fem_set=fs, stiff=stiff, parent=fem))
    return a


def connectors() -> ada.Assembly:
    """CONN3D2 connectors between two plates, with connector behaviours covering linear and
    nonlinear elasticity, linear and tabular damping, plasticity and rigid DOFs, and one carried
    verbatim (``str_override``); orientations by coordinates and by nodes."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    _plate(fem, mat, node_start=11, el_start=11, z=0.5, name="plate_top")
    elastic_nl = [[-1e5, -0.01], [0.0, 0.0], [1e5, 0.01]]
    linear = ConnectorSection("cs_linear", elastic_comp=1e6, damping_comp=1e3, parent=fem)
    comps = ConnectorSection(
        "cs_components",
        elastic_comp=[1e6, 2e6, 3e6, elastic_nl, 1e5, 1e5],
        damping_comp=[1e3, [[0.0, 0.0], [1.0, 10.0]]],
        plastic_comp=[[(1e4, 0.0, 0.0), (2e4, 0.1, 0.0)]],
        rigid_dofs=[4, 5, 6],
        parent=fem,
    )
    verbatim = ConnectorSection("cs_verbatim", parent=fem)
    verbatim.str_override = "*Connector Behavior, name=cs_verbatim\n*Connector Elasticity, component=1\n1.0E+06,"
    for cs in (linear, comps, verbatim):
        fem.add_connector_section(cs)
    n = fem.nodes.from_id
    csys = Csys("con_csys", coords=[(1, 0, 0), (0, 1, 0), (0, 0, 1)], parent=fem)
    fem.add_connector(Connector("con_a", 501, n(3), n(13), "bushing", linear, parent=fem))
    fem.add_connector(Connector("con_b", 502, n(6), n(16), "bushing", comps, csys=csys, parent=fem))
    by_nodes = Csys("con_csys_nodes", definition="NODES", nodes=[n(1), n(2), n(4)], parent=fem)
    fem.add_connector(Connector("con_c", 503, n(1), n(11), "bushing", linear, csys=by_nodes, parent=fem))
    fem.add_connector(Connector("con_d", 504, n(2), n(12), "bushing", verbatim, parent=fem))
    return a


# ── constraints, surfaces, interactions ──────────────────────────────────────────────────────


def constraints() -> ada.Assembly:
    """Tie, kinematic coupling (onto a node set, and onto a surface with an orientation), MPC,
    rigid body and shell-to-solid coupling -- at part level."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    _plate(fem, mat, node_start=11, el_start=11, z=0.01, name="plate_b")
    _unit_hex(fem, mat, node_start=31, el_start=31, x0=3.0)
    n = fem.nodes.from_id
    _nodes(fem, [(1, 0.5, 1.0)], 50)
    ref = _nset(fem, "ref", [n(50)])
    ref2 = _nset(fem, "ref2", [n(50)])
    master_nodes = _nset(fem, "tie_m", [n(i) for i in (1, 2, 3)])
    slave_nodes = _nset(fem, "tie_s", [n(i) for i in (11, 12, 13)])
    tie_m = fem.add_surface(Surface("tie_m_surf", Surface.TYPES.NODE, master_nodes, 1.0, parent=fem))
    tie_s = fem.add_surface(Surface("tie_s_surf", Surface.TYPES.NODE, slave_nodes, 1.0, parent=fem))
    fem.add_constraint(Constraint("tie1", Constraint.TYPES.TIE, tie_m, tie_s, pos_tol=0.05, parent=fem))

    cpl_nodes = _nset(fem, "cpl_nodes", [n(i) for i in (4, 5, 6)])
    fem.add_constraint(Constraint("cpl_set", Constraint.TYPES.COUPLING, ref, cpl_nodes, dofs=[1, 2, 3], parent=fem))
    cpl_surf = fem.add_surface(
        Surface("cpl_surf", Surface.TYPES.NODE, _nset(fem, "cpl_nodes_b", [n(14), n(15)]), 1.0, parent=fem)
    )
    csys = Csys("cpl_csys", coords=[(1, 0, 0), (0, 1, 0)], parent=fem)
    fem.add_constraint(
        Constraint("cpl_surf", Constraint.TYPES.COUPLING, ref2, cpl_surf, dofs=[(1, 3), 4], csys=csys, parent=fem)
    )

    m = _nset(fem, "mpc_m", [n(1), n(2)])
    s = _nset(fem, "mpc_s", [n(11), n(12)])
    fem.add_constraint(Constraint("mpc1", Constraint.TYPES.MPC, m, s, mpc_type="BEAM", parent=fem))

    rb_ref = _nset(fem, "rb_ref", [n(50)])
    rb_els = _elset(fem, "rb_els", [fem.elements.from_id(11), fem.elements.from_id(12)])
    fem.add_constraint(Constraint("rb1", Constraint.TYPES.RIGID_BODY, rb_ref, rb_els, parent=fem))

    shell_edge = _nset(fem, "s2s_shell", [n(3), n(6)])
    solid_face = _elset(fem, "s2s_solid", [fem.elements.from_id(31)])
    fem.add_constraint(
        Constraint("s2s", Constraint.TYPES.SHELL2SOLID, shell_edge, solid_face, influence_distance=0.2, parent=fem)
    )
    return a


def constraints_equation() -> ada.Assembly:
    """An *Equation constraint (Constraint.TYPES.EQUATION): the writer has no branch for it."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    n = fem.nodes.from_id
    m = _nset(fem, "eq_m", [n(1)])
    s = _nset(fem, "eq_s", [n(2)])
    fem.add_constraint(
        Constraint(
            "eq1",
            Constraint.TYPES.EQUATION,
            m,
            s,
            equation_terms=[(s, 1, 1.0), (m, 1, -1.0)],
            parent=fem,
        )
    )
    return a


def constraints_assembly_level() -> ada.Assembly:
    """A coupling written in the assembly block, referring into the instance."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    n = fem.nodes.from_id
    rp = a.fem.nodes.add(Node((1, 0.5, 1.0), 900, parent=a.fem))
    ref = a.fem.add_set(FemSet("asm_ref", [a.fem.nodes.from_id(900)], "nset", parent=a.fem))
    slaves = fem.add_set(FemSet("asm_cpl", [n(1), n(2), n(3)], "nset", parent=fem))
    a.fem.add_constraint(Constraint("asm_cpl", Constraint.TYPES.COUPLING, ref, slaves, dofs=[1, 2, 3], parent=a.fem))
    assert rp is not None
    return a


def surfaces() -> ada.Assembly:
    """Element surfaces on a solid face (S<n>) and on both shell sides (SPOS/SNEG), a node
    surface with a weight, a surface over several sets, and one given as raw id references."""
    a, p, mat = _model()
    fem = p.fem
    els, es = _plate(fem, mat)
    hexes, hes = _unit_hex(fem, mat, node_start=21, el_start=21, x0=3.0)
    fem.add_surface(Surface("solid_s2", Surface.TYPES.ELEMENT, hes, el_face_index=1, parent=fem))
    fem.add_surface(Surface("shell_pos", Surface.TYPES.ELEMENT, es, el_face_index=0, parent=fem))
    es2 = _elset(fem, "plate_first", els[:1])
    fem.add_surface(Surface("shell_neg", Surface.TYPES.ELEMENT, es2, el_face_index=-1, parent=fem))
    nodes = _nset(fem, "surf_nodes", [fem.nodes.from_id(i) for i in (1, 2)])
    fem.add_surface(Surface("node_surf", Surface.TYPES.NODE, nodes, 2.0, parent=fem))
    es3 = _elset(fem, "plate_second", els[1:])
    fem.add_surface(Surface("multi_set", Surface.TYPES.ELEMENT, [es2, es3], el_face_index=[0, 0], parent=fem))
    fem.add_surface(Surface("by_ids", Surface.TYPES.ELEMENT, es, id_refs=[(1, "SPOS"), (2, "SPOS")], parent=fem))
    return a


def interactions() -> ada.Assembly:
    """Interaction properties (friction, hard and tabular pressure-overclosure), a contact pair
    with small-sliding/adjust/geometric-correction options, general contact, and surface
    smoothing."""
    a, p, mat = _model()
    fem = p.fem
    els, es = _plate(fem, mat)
    top_els, top_es = _plate(fem, mat, node_start=11, el_start=11, z=0.01, name="plate_top")
    s1 = fem.add_surface(Surface("contact_bot", Surface.TYPES.ELEMENT, es, el_face_index=0, parent=fem))
    s2 = fem.add_surface(Surface("contact_top", Surface.TYPES.ELEMENT, top_es, el_face_index=-1, parent=fem))
    hard = a.fem.add_interaction_property(InteractionProperty("hard", friction=0.3))
    tab = a.fem.add_interaction_property(
        InteractionProperty("tabular", friction=0.1, pressure_overclosure="TABULAR", tabular=[(0.0, 0.0), (1e6, 1e-3)])
    )
    a.fem.metadata["surf_smoothing"] = [dict(name="smooth1", bulk="contact_top, 0.1")]
    a.fem.add_interaction(
        Interaction(
            "pair",
            ContactTypes.SURFACE,
            s1,
            s2,
            hard,
            metadata=dict(small_sliding="small sliding", adjust=0.0, geometric_correction="NONE"),
        )
    )
    a.fem.add_interaction(Interaction("general", ContactTypes.GENERAL, None, None, tab))
    a.fem.add_interaction(Interaction("penalty", ContactTypes.SURFACE, s2, s1, hard, constraint="PENALTY"))
    a.fem.add_interaction(
        Interaction("raw", ContactTypes.SURFACE, s1, s2, hard, metadata=dict(aba_bulk="** raw interaction text"))
    )
    return a


# ── amplitudes, initial conditions, BCs, loads ───────────────────────────────────────────────


def amplitudes() -> ada.Assembly:
    """A short tabular amplitude and a smoothed one long enough to wrap its data lines."""
    a, p, mat = _model()
    _plate(p.fem, mat)
    a.fem.add_amplitude(Amplitude("ramp", [0.0, 1.0], [0.0, 1.0]))
    xs = [0.1 * i for i in range(8)]  # 8 points: the last pair closes a full data line
    a.fem.add_amplitude(Amplitude("wave", xs, [float(np.sin(x)) for x in xs], smooth=0.05))
    return a


def initial_conditions() -> ada.Assembly:
    """A *Initial Conditions, type=VELOCITY predefined field."""
    a, p, mat = _model()
    _plate(p.fem, mat)
    moving = _nset(p.fem, "moving", [p.fem.nodes.from_id(i) for i in (1, 2, 3)])
    a.fem.add_predefined_field(PredefinedField("v0", "VELOCITY", moving, dofs=[1, 2, 3], magnitude=[1.0, 0.0, 0.5]))
    return a


def boundary_conditions() -> ada.Assembly:
    """Displacement BCs on selected DOFs with and without magnitudes, a named ENCASTRE, a
    velocity BC with an amplitude, and connector displacement / velocity motion."""
    a, p, mat = _model()
    fem = p.fem
    _plate(fem, mat)
    _plate(fem, mat, node_start=11, el_start=11, z=0.5, name="plate_top")
    cs = fem.add_connector_section(ConnectorSection("cs", elastic_comp=1e6, parent=fem))
    n = fem.nodes.from_id
    con = fem.add_connector(Connector("con", 501, n(3), n(13), "bushing", cs, parent=fem))
    fix = _nset(fem, "fix", [n(1), n(4)])
    fem.add_bc(Bc("pinned", fix, [1, 2, 3]))
    fem.add_bc(Bc("clamped", _nset(fem, "clamp", [n(11)]), ["ENCASTRE"], bc_type=Bc.TYPES.ENCASTRE))
    fem.add_bc(Bc("pushed", _nset(fem, "push", [n(2)]), [1, 3], magnitudes=[0.01, -0.02]))
    ramp = a.fem.add_amplitude(Amplitude("ramp", [0.0, 1.0], [0.0, 1.0]))
    fem.add_bc(
        Bc("moving", _nset(fem, "moving", [n(5)]), [2], magnitudes=[1.0], bc_type=Bc.TYPES.VELOCITY, amplitude=ramp)
    )
    con_set = fem.add_set(FemSet("con_el", [con], "elset", parent=fem))
    fem.add_bc(Bc("con_disp", con_set, [1], magnitudes=[0.001], bc_type=Bc.TYPES.CONN_DISPL))
    fem.add_bc(Bc("con_vel", con_set, [2], magnitudes=[0.5], bc_type=Bc.TYPES.CONN_VEL))
    return a


def loads() -> ada.Assembly:
    """Gravity, a point force + moment (follower, with an amplitude), a point load in a local
    coordinate system (which writes a *Transform), a pressure, and an acceleration field."""
    a, p, mat = _model()
    fem = p.fem
    els, es = _plate(fem, mat)
    n = fem.nodes.from_id
    step = _static_step(a)
    step.add_load(LoadGravity("grav", -9.81))
    tip = _nset(fem, "tip", [n(3)])
    ramp = a.fem.add_amplitude(Amplitude("ramp", [0.0, 1.0], [0.0, 1.0]))
    step.add_load(LoadPoint("tip_load", 100.0, tip, [0, 0, -1, 0.5, 0, 0], amplitude=ramp.name, follower_force=True))
    local = _nset(fem, "local", [n(6)])
    csys = Csys("load_csys", coords=[(0, 1, 0), (-1, 0, 0), (0, 0, 1)], parent=fem)
    step.add_load(LoadPoint("local_load", 50.0, local, [1, 0, 0, 0, 0, 0], follower_force=False, csys=csys))
    surf = fem.add_surface(Surface("press_surf", Surface.TYPES.ELEMENT, es, el_face_index=0, parent=fem))
    step.add_load(LoadPressure("press", 1000.0, surf))
    step.add_load(Load("acc", Load.TYPES.ACC, 2.0, dof=[1, 0, 0]))
    return a


# ── steps and outputs ───────────────────────────────────────────────────────────────────────


def _bc_model() -> tuple[ada.Assembly, ada.Part, FemSet]:
    a, p, mat = _model()
    _plate(p.fem, mat)
    fix = _nset(p.fem, "fix", [p.fem.nodes.from_id(i) for i in (1, 4)])
    p.fem.add_bc(Bc("fix", fix, [1, 2, 3, 4, 5, 6]))
    return a, p, fix


def steps_static() -> ada.Assembly:
    """Two static steps (nlgeom off and on, one with a restart request and appended text)."""
    a, p, _ = _bc_model()
    s1 = _static_step(a, "lin")
    s1.add_bc(Bc("step_bc", _nset(p.fem, "step_fix", [p.fem.nodes.from_id(2)]), [3], magnitudes=[0.001]))
    # Its own options object: Step.__init__ defaults solver_options to ONE shared
    # StepSolverOptions(), so setting restart_int on the shared one would change every step in
    # the process -- including steps built earlier -- and break this zoo's determinism.
    s1.options = StepSolverOptions()
    s1.options.ABAQUS.restart_int = 5
    s2 = _static_step(a, "nonlin", nl_geom=True, metadata=dict(append="** appended to the step"))
    s2.options = StepSolverOptions()
    s2.options.ABAQUS.stabilize = Stabilize(0.0002, 0.05, StabilizeTypes.DAMPING)
    return a


def steps_dynamic_implicit() -> ada.Assembly:
    a, p, _ = _bc_model()
    a.fem.add_step(StepImplicitDynamic("dyn", nl_geom=True, total_time=1.0, init_incr=0.01, max_incr=0.1))
    return a


def steps_explicit() -> ada.Assembly:
    """An explicit first step: turns *Constraint Controls off and pulls interactions into the step."""
    a, p, mat = _model()
    fem = p.fem
    els, es = _plate(fem, mat)
    top_els, top_es = _plate(fem, mat, node_start=11, el_start=11, z=0.01, name="plate_top")
    s1 = fem.add_surface(Surface("bot", Surface.TYPES.ELEMENT, es, el_face_index=0, parent=fem))
    s2 = fem.add_surface(Surface("top", Surface.TYPES.ELEMENT, top_es, el_face_index=-1, parent=fem))
    prop = a.fem.add_interaction_property(InteractionProperty("fric", friction=0.2))
    a.fem.add_step(StepExplicit("impact", total_time=0.01))
    a.fem.add_interaction(Interaction("pair", ContactTypes.SURFACE, s1, s2, prop))
    return a


def steps_eigen() -> ada.Assembly:
    a, p, _ = _bc_model()
    a.fem.add_step(StepEigen("eig", num_eigen_modes=10))
    return a


def steps_complex_eigen() -> ada.Assembly:
    a, p, _ = _bc_model()
    a.fem.add_step(StepEigen("eig", num_eigen_modes=10))
    a.fem.add_step(StepEigenComplex("complex_eig", num_eigen_modes=10))
    return a


def steps_steady_state() -> ada.Assembly:
    a, p, _ = _bc_model()
    tip = _nset(p.fem, "tip", [p.fem.nodes.from_id(3)])
    unit = LoadPoint("unit", 1.0, tip, [None, None, 1, None, None, None])
    a.fem.add_step(StepEigen("eig", num_eigen_modes=10))
    a.fem.add_step(StepSteadyState("ssd", unit, fmin=1.0, fmax=10.0))
    return a


def steps_raw_input() -> ada.Assembly:
    """A step whose text is supplied verbatim (metadata ``aba_inp``)."""
    a, p, _ = _bc_model()
    raw = "*Step, name=raw\n*Static\n0.1, 1.0\n*End Step\n"
    _static_step(a, "raw", metadata=dict(aba_inp=raw))
    return a


def outputs() -> ada.Assembly:
    """Field output with nodal/element/contact variables, and history output of each type:
    node, connector (element), energy and contact."""
    a, p, mat = _model()
    fem = p.fem
    els, es = _plate(fem, mat)
    top_els, top_es = _plate(fem, mat, node_start=11, el_start=11, z=0.01, name="plate_top")
    cs = fem.add_connector_section(ConnectorSection("cs", elastic_comp=1e6, parent=fem))
    n = fem.nodes.from_id
    con = fem.add_connector(Connector("con", 501, n(3), n(13), "bushing", cs, parent=fem))
    s1 = fem.add_surface(Surface("bot", Surface.TYPES.ELEMENT, es, el_face_index=0, parent=fem))
    s2 = fem.add_surface(Surface("top", Surface.TYPES.ELEMENT, top_es, el_face_index=-1, parent=fem))
    step = _static_step(a)
    step.add_field_output(
        FieldOutput("fields", nodal=["U", "RF"], element=["S", "E"], contact=["CSTRESS"], int_value=2)
    )
    step.add_field_output(FieldOutput("elements_only", nodal=[], element=["S"], contact=[], int_type="time interval"))
    tip = _nset(fem, "tip", [n(3)])
    step.add_history_output(HistOutput("tip_u", tip, "node", ["U1", "U2", "U3"]))
    con_set = fem.add_set(FemSet("con_el", [con], "elset", parent=fem))
    step.add_history_output(HistOutput("con_f", con_set, "connector", ["CTF1", "CU1"]))
    step.add_history_output(HistOutput("energy", es, "energy", ["ALLIE", "ALLKE"]))
    # Contact history output takes a (slave, master) surface PAIR, which Step.add_history_output
    # cannot accept (it expects one set with a .parent) -- so it is appended to the step's list
    # directly, the only way to reach the writer's contact-output branch.
    contact = HistOutput("contact", [s1, s2], "contact", ["CFN"])
    contact.parent = step
    step.hist_outputs.append(contact)
    return a


# ── assembly structure ───────────────────────────────────────────────────────────────────────


def multi_part() -> ada.Assembly:
    """Two parts (two instances) and nodes/masses/sets defined at assembly level.

    NB: through ``Assembly.to_fem`` this is merged into ONE part first (general.write_to_fem does
    that for every format once more than one part has nodes); only a direct call of the Abaqus
    writer reaches its part/instance/assembly-level code with this model."""
    a, p, mat = _model()
    _plate(p.fem, mat)
    p2 = ada.Part("P2")
    a.add_part(p2)
    mat2 = p2.add_material(ada.Material("S420", CarbonSteel("S420")))
    _unit_hex(p2.fem, mat2, x0=3.0)

    afem = a.fem
    afem.nodes.add(Node((5, 0, 0), 801, parent=afem))
    afem.nodes.add(Node((5, 1, 0), 802, parent=afem))
    m_set = afem.add_set(FemSet("asm_mass_nodes", [afem.nodes.from_id(801)], "nset", parent=afem))
    afem.add_mass(Mass("asm_mass", m_set, 3.0, mass_id=901))
    return a


def reference_point() -> ada.Assembly:
    """A reference point (FEM.add_rp) on a single-part model."""
    a, p, mat = _model()
    _plate(p.fem, mat)
    p.fem.add_rp("rp1", Node((1, 0.5, 2.0), 700))
    return a


def read_back_deck() -> ada.Assembly:
    """What the Abaqus READER hands the writer: a repo deck read with ``from_fem``."""
    return ada.from_fem(REPO_FILES / "fem_files" / "abaqus" / "box.inp", "abaqus")


ZOO: dict[str, Callable[[], ada.Assembly]] = {
    f.__name__: f
    for f in (
        elements_shell,
        elements_shell_second_order,
        elements_shell_tri7,
        elements_shell_tri6,
        elements_solid_first_order,
        elements_solid_second_order,
        elements_solid_wedge,
        elements_line_profiles,
        elements_line_verbatim,
        elements_line_second_order,
        elements_line_explicit,
        sections_zero_thickness,
        sets,
        sets_empty,
        materials,
        masses,
        masses_anisotropic,
        springs,
        springs_coupled,
        springs_two_node,
        connectors,
        constraints,
        constraints_equation,
        constraints_assembly_level,
        surfaces,
        interactions,
        amplitudes,
        initial_conditions,
        boundary_conditions,
        loads,
        steps_static,
        steps_dynamic_implicit,
        steps_explicit,
        steps_eigen,
        steps_complex_eigen,
        steps_steady_state,
        steps_raw_input,
        outputs,
        multi_part,
        reference_point,
        read_back_deck,
    )
}
