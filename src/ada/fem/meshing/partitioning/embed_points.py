"""Mesh nodes at given points: the concept point loads and point supports of a part.

gmsh puts nodes where the geometry has vertices and spreads the rest evenly, so a point in a beam's span or inside a
plate gets no node of its own. GeniE does make one: its own mesh of the support fixture had a node at ``Sp_mid``
(1.3, 1.5, 0) in a 0.5 m mesh of a 4 m beam, where adapy's had none and the support acted on nothing. Here every
such point is made a vertex of the geometry it lies on before meshing -- the curves and surfaces it is on are
fragmented with it (``occ.fragment``), which splits a curve there and embeds the point in a surface -- so the mesh
has a node exactly there.

Only the entities a point lies on are touched, and a point that already is a vertex changes nothing. All the
hosting entities, of every object holding one of them, go through one ``fragment`` call together: a curve shared by
a beam and the plate it bounds is split once, for both, so the mesh stays conformal.

A quad mesh (``use_quads``) meshes each plate as a transfinite surface, which takes no extra vertex: measured with
gmsh 4.15.2, a point inside a plate got no node, and a point on a plate's edge stopped the mesh with "Surface 1 is
transfinite but has 5 corners". So with ``surfaces=False`` a point on a surface (edge or inside) is not embedded and
is said so; the conversion that wanted a node there then reports the load or support that acts on nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ..concepts import GmshSession


def embed_points(gmsh_session: GmshSession, points: Iterable, tol: float = 1e-4, surfaces: bool = True) -> int:
    """Make each of ``points`` (global coordinates) a vertex of every curve and surface it lies on.

    Returns how many points became new vertices. A point on no meshed geometry is left alone: the conversion that
    asked for it finds no node there and reports it. With ``surfaces=False`` a point on any surface is left alone
    too (see the module docstring).
    """
    from ada.fem.concept.to_fem import STAGE, report

    model = gmsh_session.model
    model.occ.synchronize()
    vertices = np.array([model.getValue(0, t, []) for _, t in model.getEntities(0)]).reshape(-1, 3)

    hosts: set[tuple[int, int]] = set()
    new_points = []
    for p in _unique(points, tol):
        if len(vertices) > 0 and np.min(np.linalg.norm(vertices - p, axis=1)) <= tol:
            continue
        on = [
            (dim, tag)
            for dim in (1, 2)
            for _, tag in model.getEntities(dim)
            if np.linalg.norm(np.asarray(model.getClosestPoint(dim, tag, list(p))[0]) - p) <= tol
            and model.isInside(dim, tag, list(p)) > 0
        ]
        if not on:
            continue
        if not surfaces and any(dim == 2 for dim, _ in on):
            report().note(
                STAGE,
                "Point",
                str(tuple(round(float(c), 6) for c in p)),
                "a load or support point on a plate is not made a mesh vertex in a quad mesh, a transfinite surface "
                "taking no extra vertex; it has a node only where the quad mesh happens to put one",
            )
            continue
        hosts.update(on)
        new_points.append(p)

    if not new_points:
        return 0

    objects = []
    for data in gmsh_session.model_map.values():
        if any(tuple(e) in hosts for e in data.entities):
            objects += [tuple(e) for e in data.entities if tuple(e) not in objects]
    tools = [(0, model.occ.addPoint(*p)) for p in new_points]
    _, res_map = model.occ.fragment(objects, tools, removeObject=True, removeTool=True)
    children = {obj: [c for c in res_map[i] if c[0] == obj[0]] for i, obj in enumerate(objects)}
    for data in gmsh_session.model_map.values():
        if any(tuple(e) in children for e in data.entities):
            new = []
            for e in data.entities:
                for c in children.get(tuple(e), [tuple(e)]):
                    if c not in new:
                        new.append(c)
            data.entities = new
    model.occ.synchronize()
    logger.info(f"Embedded {len(new_points)} point(s) as mesh vertices")
    return len(new_points)


def _unique(points: Iterable, tol: float) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for p in points:
        p = np.asarray(p, dtype=float).reshape(3)
        if all(np.linalg.norm(p - q) > tol for q in out):
            out.append(p)
    return out
