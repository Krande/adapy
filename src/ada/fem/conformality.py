from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada.api.nodes import Node
    from ada.fem import FEM, Elem


class NonConformalMeshError(Exception):
    """Raised when a mesh contains hanging (T-junction) nodes and the meshing config
    is set to escalate (Config().meshing_raise_on_hanging_nodes)."""


class HangingNode(NamedTuple):
    node: Node
    elem: Elem
    edge: tuple[Node, Node]


def find_hanging_nodes(fem: FEM, tol: float = 1e-4) -> list[HangingNode]:
    """Detect hanging (non-conformal) nodes in the shell mesh.

    A hanging node lies on the *interior* of a shell element edge without being one of
    that element's nodes — the topological signature of a T-junction where two adjacent
    plates failed to imprint their shared edge, leaving the mesh disconnected. A
    conformal mesh has none: every shared-edge node belongs to the elements on both
    sides. Pure-numpy spatial hash (no scipy dependency in the meshing hot path).

    Returns a list of ``(node, elem, (edge_n0, edge_n1))`` incidences.
    """
    shells = list(fem.elements.shell)
    if len(shells) < 2:
        return []

    # Distinct mesh nodes referenced by shell elements.
    node_by_id: dict[int, Node] = {}
    for el in shells:
        for n in el.nodes:
            node_by_id[n.id] = n
    nodes = list(node_by_id.values())
    coords = np.array([n.p for n in nodes], dtype=float)
    ids = [n.id for n in nodes]

    # Spatial hash grid: cell sized so each (element-scale) edge spans only a handful of
    # cells — bbox extent / cbrt(N) gives ~1 node per cell on average.
    origin = coords.min(axis=0)
    extent = float(np.max(coords.max(axis=0) - origin))
    cell = extent / max(round(len(nodes) ** (1 / 3)), 1)
    if cell <= 0:
        cell = 1.0

    grid: dict[tuple[int, int, int], list[int]] = {}
    for i, c in enumerate(np.floor((coords - origin) / cell).astype(int)):
        grid.setdefault((int(c[0]), int(c[1]), int(c[2])), []).append(i)

    hanging: list[HangingNode] = []
    seen: set[tuple[int, frozenset]] = set()

    for el in shells:
        try:
            edge_nodes = el.shape.edges  # flat [n0, n1, n1, n2, ...] corner edges
        except ValueError:
            continue  # element type without a defined edge sequence — skip
        el_node_ids = {n.id for n in el.nodes}

        for k in range(0, len(edge_nodes) - 1, 2):
            a, b = edge_nodes[k], edge_nodes[k + 1]
            pa = np.asarray(a.p, dtype=float)
            pb = np.asarray(b.p, dtype=float)
            d = pb - pa
            l2 = float(d.dot(d))
            if l2 == 0.0:
                continue
            t_eps = tol / np.sqrt(l2)  # exclude the endpoint neighbourhoods

            lo = np.floor((np.minimum(pa, pb) - origin - tol) / cell).astype(int)
            hi = np.floor((np.maximum(pa, pb) - origin + tol) / cell).astype(int)
            for cx in range(int(lo[0]), int(hi[0]) + 1):
                for cy in range(int(lo[1]), int(hi[1]) + 1):
                    for cz in range(int(lo[2]), int(hi[2]) + 1):
                        for idx in grid.get((cx, cy, cz), ()):
                            nid = ids[idx]
                            if nid in el_node_ids:
                                continue
                            q = coords[idx]
                            t = float((q - pa).dot(d)) / l2
                            if t <= t_eps or t >= 1.0 - t_eps:
                                continue  # not strictly interior to the edge
                            if np.linalg.norm(q - (pa + t * d)) > tol:
                                continue  # off the edge line
                            key = (int(nid), frozenset((a.id, b.id)))
                            if key in seen:
                                continue
                            seen.add(key)
                            hanging.append(HangingNode(node_by_id[nid], el, (a, b)))

    return hanging


def check_conformal_mesh(fem: FEM, raise_on_fail: bool = False, tol: float = 1e-4) -> list[HangingNode]:
    """Run :func:`find_hanging_nodes` and report. Warns by default; raises
    :class:`NonConformalMeshError` when ``raise_on_fail`` is set."""
    hanging = find_hanging_nodes(fem, tol=tol)
    if not hanging:
        return hanging

    sample = ", ".join(str(tuple(round(float(x), 4) for x in h.node.p)) for h in hanging[:10])
    msg = (
        f"Non-conformal mesh: {len(hanging)} hanging node(s) on shared shell edges "
        f"(T-junctions — adjacent plates not imprinted). First coords: {sample}"
    )
    if raise_on_fail:
        raise NonConformalMeshError(msg)
    logger.warning(msg)
    return hanging
