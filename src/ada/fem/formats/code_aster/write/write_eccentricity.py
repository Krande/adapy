"""Beam eccentricities for Code_Aster: the beam on nodes moved to its axis, tied to the mesh nodes by rigid links.

An eccentric beam element (``Elem.eccentricity``: a stiffener meshed on its plate's nodes with its neutral axis
``h - Cgz`` below the plate, Sesam's GECCEN) has no counterpart in Code_Aster's beams. ``POU_D_E``/``POU_D_T`` are
placed on their nodes and ``AFFE_CARA_ELEM``/``POUTRE`` has no offset keyword (``EXCENTREMENT`` is ``COQUE``'s and
``GRILLE``'s, U4.42.01, checked in the 18.1 catalogue). So the deck does what Sestra does with a GECCEN inside: the
beam's elements are written on **offset nodes** -- the mesh node plus its eccentricity vector, on the neutral axis --
and each offset node is tied to the mesh node it came from by a **rigid link**:

    u_axis = u_node + theta_node x e,   theta_axis = theta_node

written as ``AFFE_CHAR_MECA``/``LIAISON_GROUP`` (U4.44.01): one item per dof for every distinct offset vector, the
nodes of the two groups paired by ``TRAN`` = the vector. A ``LIAISON_SOLIDE`` per node pair or a ``LIAISON_DDL`` would
need a node group per node (``NOEUD`` is no longer a user keyword); ``LIAISON_GROUP`` ties a whole stiffener's nodes
in six items. The relations are dualised (Lagrange multipliers) like the supports, exact in linear theory: for a
straight beam a rigid offset of its ends is the same stiffness as a section eccentric to the reference line, which is
how Sestra, GeniE and Abaqus (``*CENTROID``) model it.

The offset nodes exist in the MED file only (:mod:`.write_med`, :class:`EccentricMesh`); the model's FEM is not
changed, and the other writers keep their own form of the eccentricity (Sesam: GECCEN). Their ids follow the mesh's
last node, and ``<name>.ecc_nodes.json`` beside the deck maps each offset node to its mesh node, for the SIN
conversion (:mod:`..results.to_sin`), which finds a MED beam in the Sesam deck by its nodes.

The reference node has to carry rotations (a shell's or a beam's node; a solid's has none), which is where an
eccentric beam sits in practice. A truss (``BARRE``) is tied in its translations only: it has no rotations to tie.

Measured (Code_Aster 18.1.7, Sestra V11.3-01, the same adapy mesh): a 7.8 x 3.9 m simply supported panel, 6 mm plate,
HP160x8 stiffeners at 0.65 m and TG600x300x20x32 girders on the long edges under 50 kPa -- see ``PR.md`` /
``tests/fem/test_code_aster_eccentric_beams.py`` for the numbers.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada.api.spatial import Part
    from ada.fem import FEM

#: The charge of all rigid links, in every step's ``EXCIT`` (:func:`.write_constraints.step_charges`).
ECC_LINKS = "ecc_links"

#: The node groups of the links: ``<prefix><k>_ref`` (mesh nodes) and ``<prefix><k>_axis`` (offset nodes) per vector.
GROUP_PREFIX = "ecc_"

_ROUND = 9


@dataclass
class EccentricLink:
    """The nodes of one distinct offset vector: mesh nodes and the offset nodes ``vector`` away from them."""

    vector: tuple[float, float, float]
    #: whether the offset nodes carry rotations (a beam's do, a truss's or a cable's do not)
    rotations: bool
    ref_group: str
    axis_group: str
    ref_ids: list[int] = field(default_factory=list)
    axis_ids: list[int] = field(default_factory=list)


@dataclass
class EccentricMesh:
    """What the MED file and the command file need for the eccentric beams of a model."""

    #: offset node id -> its position (mesh node + vector)
    coords: dict[int, np.ndarray]
    #: offset node id -> the mesh node it is tied to
    ref_of: dict[int, int]
    #: element id -> {mesh node id: offset node id} for the nodes the element takes off the mesh line
    substitutions: dict[int, dict[int, int]]
    links: list[EccentricLink]

    @property
    def node_groups(self) -> dict[str, list[int]]:
        out = {}
        for link in self.links:
            out[link.ref_group] = link.ref_ids
            out[link.axis_group] = link.axis_ids
        return out

    def element_nodes(self, el_id: int, node_ids: list[int]) -> list[int]:
        """``node_ids`` of element ``el_id`` with its offset nodes substituted."""
        sub = self.substitutions.get(el_id)
        if sub is None:
            return node_ids
        return [sub.get(n, n) for n in node_ids]

    def dump(self, path: pathlib.Path) -> None:
        payload = {
            "version": 1,
            "ref_of": {str(k): int(v) for k, v in self.ref_of.items()},
            "links": [
                {"vector": list(lk.vector), "ref_group": lk.ref_group, "axis_group": lk.axis_group} for lk in self.links
            ],
        }
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def eccentric_elements(fem: FEM):
    """The line elements the deck writes on offset nodes: sectioned, with an eccentricity that is not all zero."""
    for el in fem.elements.lines_ecc:
        if el.fem_sec is None:
            continue
        if any(_end_vector(el.eccentricity, i) is not None for i in (0, 1)):
            yield el


def _end_vector(ecc, end: int) -> np.ndarray | None:
    pt = ecc.end1 if end == 0 else ecc.end2
    if pt is None or pt.ecc_vector is None:
        return None
    vec = np.asarray(pt.ecc_vector, dtype=float)
    return vec if np.any(np.abs(vec) > 10**-_ROUND) else None


def _free_prefix(part: Part) -> str:
    """:data:`GROUP_PREFIX`, or ``ecc2_``, ``ecc3_``, ... when a node set of the model starts with it."""
    taken = set(part.fem.nsets.keys())
    assembly = part.get_assembly()
    if assembly is not part:
        taken |= set(assembly.fem.nsets.keys())
    prefix, n = GROUP_PREFIX, 2
    while any(name.startswith(prefix) for name in taken):
        prefix = f"ecc{n}_"
        n += 1
    return prefix


def eccentric_mesh(part: Part) -> EccentricMesh | None:
    """The offset nodes, substitutions and links of ``part``'s eccentric beams; None when it has none.

    An element's end nodes take the vectors of its eccentricity ends; a mid node (LINE3) their mean. A mesh node
    shared by consecutive elements of one beam gets one offset node per distinct vector, so the beam stays
    continuous on its axis; a node two beams cross at with different offsets gets one per beam. The links are
    grouped by vector and by whether the elements carry rotations, since one ``LIAISON_GROUP`` ties all its nodes
    alike.
    """
    fem = part.fem
    elements = list(eccentric_elements(fem))
    if not elements:
        return None

    next_id = int(fem.nodes.max_nid) + 1
    offset_of: dict[tuple[int, tuple, bool], int] = {}
    coords: dict[int, np.ndarray] = {}
    ref_of: dict[int, int] = {}
    substitutions: dict[int, dict[int, int]] = {}
    by_vector: dict[tuple[tuple, bool], tuple[list[int], list[int]]] = {}

    for el in elements:
        nodes = list(el.nodes)
        ecc = el.eccentricity
        rotations = not el.fem_sec.is_axial_only
        v1 = _end_vector(ecc, 0)
        v2 = _end_vector(ecc, 1)
        # An eccentricity end names its node; a vector given for a node that is not the element's own end is the
        # Sesam writer's "omitted" case, and is ignored here the same way.
        if v1 is not None and ecc.end1.node.id != nodes[0].id:
            v1 = None
        if v2 is not None and ecc.end2.node.id != nodes[-1].id:
            v2 = None
        zero = np.zeros(3)
        a, b = (v1 if v1 is not None else zero), (v2 if v2 is not None else zero)
        sub: dict[int, int] = {}
        for i, node in enumerate(nodes):
            if i == 0:
                vec = a
            elif i == len(nodes) - 1:
                vec = b
            else:
                vec = 0.5 * (a + b)
            if not np.any(np.abs(vec) > 10**-_ROUND):
                continue
            key_vec = tuple(round(float(c), _ROUND) for c in vec)
            key = (int(node.id), key_vec, rotations)
            off_id = offset_of.get(key)
            if off_id is None:
                off_id = next_id
                next_id += 1
                offset_of[key] = off_id
                coords[off_id] = np.asarray(node.p, dtype=float) + np.asarray(key_vec)
                ref_of[off_id] = int(node.id)
                refs, axes = by_vector.setdefault((key_vec, rotations), ([], []))
                refs.append(int(node.id))
                axes.append(off_id)
            sub[int(node.id)] = off_id
        if sub:
            substitutions[int(el.id)] = sub

    prefix = _free_prefix(part)
    links = [
        EccentricLink(vec, rotations, f"{prefix}{k}_ref", f"{prefix}{k}_axis", refs, axes)
        for k, ((vec, rotations), (refs, axes)) in enumerate(by_vector.items(), start=1)
    ]
    return EccentricMesh(coords, ref_of, substitutions, links)


def _relations(vec: tuple[float, float, float], rotations: bool) -> list[tuple[tuple, tuple, str]]:
    """The six (three, for a truss) relations ``sum(c1 * ddl1(ref)) + sum(c2 * ddl2(axis)) = 0`` of a rigid link.

    ``u_axis = u_ref + theta_ref x e``: ``theta x e = (ty ez - tz ey, tz ex - tx ez, tx ey - ty ex)``.
    """
    ex, ey, ez = vec
    out = []
    for ddl, terms in (
        ("DX", (("DRY", ez), ("DRZ", -ey))),
        ("DY", (("DRZ", ex), ("DRX", -ez))),
        ("DZ", (("DRX", ey), ("DRY", -ex))),
    ):
        ddl_1 = [ddl] + [name for name, c in terms if c != 0.0]
        coef_1 = [1.0] + [c for _, c in terms if c != 0.0]
        out.append((tuple(ddl_1), tuple(coef_1), ddl))
    if rotations:
        for ddl in ("DRX", "DRY", "DRZ"):
            out.append(((ddl,), (1.0,), ddl))
    return out


def _fmt(values) -> str:
    return "(" + ", ".join(f"{float(v):.10g}" for v in values) + ",)"


def _names(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ",)"


def ecc_links_str(ecc: EccentricMesh) -> str:
    """The ``AFFE_CHAR_MECA`` of all rigid links, :data:`ECC_LINKS`."""
    items = []
    for link in ecc.links:
        for ddl_1, coef_1, ddl_2 in _relations(link.vector, link.rotations):
            items.append(
                f"        _F(GROUP_NO_1='{link.ref_group}', GROUP_NO_2='{link.axis_group}', TRAN={_fmt(link.vector)}, "
                f"DDL_1={_names(ddl_1)}, COEF_MULT_1={_fmt(coef_1)}, "
                f"DDL_2=('{ddl_2}',), COEF_MULT_2=(-1.0,), COEF_IMPO=0.0),"
            )
    body = "\n".join(items)
    return f"""{ECC_LINKS} = AFFE_CHAR_MECA(
    MODELE=model,
    LIAISON_GROUP=(
{body}
    ),
)"""


def ecc_sidecar_path(deck_path: pathlib.Path) -> pathlib.Path:
    """``<name>.ecc_nodes.json`` beside ``<name>.med`` / ``.comm`` / ``.rmed``."""
    return deck_path.with_suffix(".ecc_nodes.json")


def load_ref_of(deck_path: pathlib.Path) -> dict[int, int]:
    """The offset node -> mesh node map a deck was written with; empty when it had no eccentric beams."""
    path = ecc_sidecar_path(deck_path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): int(v) for k, v in data.get("ref_of", {}).items()}
