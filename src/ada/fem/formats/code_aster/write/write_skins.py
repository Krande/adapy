"""Skin cells for a pressure on solid elements: the faces it acts on, as 2D cells of the MED mesh.

Code_Aster loads a solid's face with ``PRES_REP`` on *face* cells (U4.44.01): a mesh group of TRIA3 / QUAD4 /
TRIA6 / QUAD8 cells lying on the solid's skin, assigned the solid's own modelisation so they become its boundary
elements. adapy's model has no such cells -- a solid surface is an element set and an Abaqus face number per set
(:func:`ada.fem.surfaces.side_node_indices`) -- so the writer makes them, for this deck only: one cell per loaded
face, its nodes in the order that turns its normal *out* of the element (measured off the element's own centroid,
whatever the face table's winding), in one group per load.

Code_Aster's ``PRES_REP`` pushes against the cell's normal, so with the normal out a positive pressure pushes into
the solid, as Abaqus's ``*Dsload P`` on the same face does.

The cells are handed to the MED writer beside the mesh (they are not added to the model), and to the ``.comm``
writer by load through :func:`current`, while one deck is written (:func:`deck`).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator

import numpy as np

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Elem, FemSet


@dataclass
class Skins:
    #: the face cells, in the order they were made (ids after the model's own)
    elements: list = field(default_factory=list)
    #: one element set per load, named ``sk<n>`` (short: MED drops group names over 24 chars)
    sets: list = field(default_factory=list)
    #: ``id(load) -> skin set`` of the load's faces on solids
    by_load: dict = field(default_factory=dict)
    #: ``(id(load), id(element set), side as written)`` of every region the load's skin covers
    covered: set = field(default_factory=set)

    def group(self, load) -> FemSet | None:
        return self.by_load.get(id(load))

    def covers(self, load, fem_set: FemSet, side) -> bool:
        return (id(load), id(fem_set), str(side)) in self.covered


_current: list[Skins] = []


@contextlib.contextmanager
def deck(skins: Skins) -> Iterator[Skins]:
    _current.append(skins)
    try:
        yield skins
    finally:
        _current.pop()


def current() -> Skins:
    return _current[-1] if _current else Skins()


#: Face cell shape by node count: 3 / 4 corners, 6 / 8 with mid-side nodes.
def _face_shape(n: int):
    from ada.fem.shapes.definitions import ShellShapes

    return {3: ShellShapes.TRI, 4: ShellShapes.QUAD, 6: ShellShapes.TRI6, 8: ShellShapes.QUAD8}.get(n)


def _outward(nodes: list, element: Elem) -> list:
    """``nodes`` (corners in winding order, then the mid-side node of each corner-to-next edge) reordered, if need be,
    so the face's normal points away from the element's centroid."""
    corners = 3 if len(nodes) in (3, 6) else 4
    c = np.array([n.p for n in nodes[:corners]], dtype=float)
    normal = np.cross(c[1] - c[0], c[2] - c[0]) if corners == 3 else np.cross(c[2] - c[0], c[3] - c[1])
    centroid = np.mean([n.p for n in element.nodes], axis=0)
    if float(np.dot(normal, c.mean(axis=0) - centroid)) >= 0:
        return list(nodes)
    # Reverse the winding keeping the first corner: c0, c_{k-1}, ..., c1; the mid-side node of edge (c_i, c_{i+1})
    # is mid[i], so the reversed edges (c0, c_{k-1}), (c_{k-1}, c_{k-2}), ... are mid[k-1], mid[k-2], ..., mid[0].
    cs, mids = list(nodes[:corners]), list(nodes[corners:])
    out = [cs[0]] + cs[:0:-1]
    if mids:
        out += mids[::-1]
    return out


def pressure_skins(steps, fem: FEM) -> Skins:
    """The face cells of every pressure on solid elements in ``steps``.

    A pressure whose surface also names shells is left to the shell path (``FORCE_COQUE``), which refuses the
    mixture; a solid side that names no face (a blank Abaqus side: the element's free faces) is refused there too.
    """
    from ada.fem import Elem, FemSet, LoadPressure
    from ada.fem.shapes.definitions import SolidShapes
    from ada.fem.surfaces import pressure_elsets, side_node_indices

    skins = Skins()
    next_id = int(fem.elements.max_el_id) + 1 if len(fem.elements) else 1
    for step in steps:
        for load in step.loads:
            if not isinstance(load, LoadPressure) or load.surface is None or id(load) in skins.by_load:
                continue
            try:
                groups = pressure_elsets(load.surface)
            except ValueError:
                continue
            cells = []
            for fem_set, side in groups:
                members = list(fem_set.members)
                if not members or not all(isinstance(getattr(el, "type", None), SolidShapes) for el in members):
                    continue
                made = []
                for el in sorted(members, key=lambda e: e.id):
                    try:
                        slots = side_node_indices(el.type, side)
                    except ValueError:
                        slots = None
                    shape = None if slots is None else _face_shape(len(slots))
                    if shape is None:
                        made = None
                        break
                    made.append(Elem(next_id + len(made), _outward([el.nodes[s] for s in slots], el), shape))
                if not made:
                    continue
                next_id += len(made)
                cells += made
                skins.covered.add((id(load), id(fem_set), str(side)))
            if not cells:
                continue
            taken = {fs.name for fs in skins.sets}
            n = len(skins.sets) + 1
            while f"sk{n}" in fem.elsets or f"sk{n}" in taken:
                n += 1
            skin = FemSet(f"sk{n}", cells, FemSet.TYPES.ELSET)
            skins.elements += cells
            skins.sets.append(skin)
            skins.by_load[id(load)] = skin
    return skins
