"""Projecting a point onto a surface facet, and the facet's interpolation weights there.

This is the geometry an *exact* tie needs. Abaqus does not tie a secondary node to the
nearest node of the main surface; it finds the main **facet** the node projects onto and
interpolates between that facet's nodes with the facet's own shape functions. Sesam can
express exactly that, because several BLDEP records may name the same dependent node with
different independent nodes and Sesam sums them (manual §7.2.14) -- so the weights computed
here become one BLDEP per facet node.

Two numbers come out of a projection and both matter:

* the **distance** from the point to the facet, which is the measure Abaqus' ``position
  tolerance`` is actually about -- it is a distance to the *surface*, not to a node;
* the **weights**, one per facet node, which sum to 1. That sum is the load-bearing property:
  it is what makes ``u_slave = sum_k w_k (u_k + theta_k x (x_slave - x_k))`` reproduce a rigid
  body motion of the main surface exactly, whatever the weights happen to be.

Kept out of :mod:`ada.fem.surfaces` deliberately. That module answers "what is on this
surface"; this one answers "where on it does this point land", which only the tie writer
needs, and keeping it separate means the surface reader that shell-to-solid coupling shares
is not touched by any of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Sequence

import numpy as np

from ada.fem.shapes.definitions import LineShapes, ShellShapes

if TYPE_CHECKING:
    from ada.fem.surfaces import SurfaceFacet

#: A weight this small is treated as zero, so the facet node carrying it gets no BLDEP record
#: at all. Shape functions are *exactly* zero at a good many points -- every TRI6 corner
#: function vanishes at the opposite edge's mid-side node, and at a facet corner five of the
#: six vanish -- and a record whose every coefficient is 0.0 would still declare the master's
#: dofs to be read, which ``bnbcd_str`` then has to keep free for no reason. The threshold is
#: absolute rather than relative because the quantity is already dimensionless and sums to 1;
#: dropping terms below it changes that sum by at most ``n_nodes * 1e-12``.
WEIGHT_EPS = 1e-12

#: Parametric coordinates of the four corners of a quadrilateral facet, in facet node order.
_QUAD_PARAM_CORNERS = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])

#: The quadrilateral split along *both* diagonals. A QUAD8 face of a C3D20, or a QUAD shell,
#: is bilinear and in general **not planar**, so there is no plane to project onto. Each of
#: these four corner triangles is planar, and the closest of the four is taken -- see
#: :func:`_project_quad` for why that is the right answer and what it costs.
_QUAD_TRIANGLES = ((0, 1, 2), (0, 2, 3), (1, 2, 3), (1, 3, 0))


# ── shape functions ──────────────────────────────────────────────────────────────
#
# One function per facet topology, each taking the parametric coordinates of N points and
# returning an ``(N, n_nodes)`` array of weights that sums to 1 along the last axis. Node
# order is the facet's own -- corners in winding order, then the mid-side node of each of
# those edges in the same order -- which is the order ``SurfaceFacet.nodes`` guarantees.


def _line_weights(param: np.ndarray) -> np.ndarray:
    """LINE2 along ``xi`` in [-1, 1]: node 0 at -1, node 1 at +1."""
    xi = param[:, 0]
    return np.stack([(1.0 - xi) / 2.0, (1.0 + xi) / 2.0], axis=1)


def _line3_weights(param: np.ndarray) -> np.ndarray:
    """LINE3: the two corners at xi = -+1 and the mid-side node at xi = 0, in that order."""
    xi = param[:, 0]
    return np.stack([xi * (xi - 1.0) / 2.0, xi * (xi + 1.0) / 2.0, 1.0 - xi * xi], axis=1)


def _tri_weights(param: np.ndarray) -> np.ndarray:
    """TRI3: the area coordinates themselves."""
    return param[:, :3].copy()


def _tri6_weights(param: np.ndarray) -> np.ndarray:
    """TRI6: three corners, then the mid-side node of edge 0-1, 1-2, 2-0.

    Corner weights go *negative* over most of the facet -- -1/9 at the centroid -- which is
    correct for a quadratic triangle and perfectly writable as a BLDEP coefficient.
    """
    l1, l2, l3 = param[:, 0], param[:, 1], param[:, 2]
    return np.stack(
        [
            l1 * (2.0 * l1 - 1.0),
            l2 * (2.0 * l2 - 1.0),
            l3 * (2.0 * l3 - 1.0),
            4.0 * l1 * l2,
            4.0 * l2 * l3,
            4.0 * l3 * l1,
        ],
        axis=1,
    )


def _quad_weights(param: np.ndarray) -> np.ndarray:
    """QUAD4, bilinear."""
    xi, eta = param[:, 0:1], param[:, 1:2]
    xi_c, eta_c = _QUAD_PARAM_CORNERS[:, 0], _QUAD_PARAM_CORNERS[:, 1]
    return (1.0 + xi * xi_c) * (1.0 + eta * eta_c) / 4.0


def _quad8_weights(param: np.ndarray) -> np.ndarray:
    """QUAD8 serendipity: four corners, then the mid-side node of edge 0-1, 1-2, 2-3, 3-0."""
    xi, eta = param[:, 0:1], param[:, 1:2]
    xi_c, eta_c = _QUAD_PARAM_CORNERS[:, 0], _QUAD_PARAM_CORNERS[:, 1]
    corners = (1.0 + xi * xi_c) * (1.0 + eta * eta_c) * (xi * xi_c + eta * eta_c - 1.0) / 4.0
    # Mid-side nodes of edges 0-1 and 2-3 sit at xi = 0; those of 1-2 and 3-0 at eta = 0.
    mid_xi0 = (1.0 - xi * xi) * (1.0 + eta * np.array([-1.0, 1.0])) / 2.0  # edges 0-1, 2-3
    mid_eta0 = (1.0 + xi * np.array([1.0, -1.0])) * (1.0 - eta * eta) / 2.0  # edges 1-2, 3-0
    return np.concatenate([corners, mid_xi0[:, 0:1], mid_eta0[:, 0:1], mid_xi0[:, 1:2], mid_eta0[:, 1:2]], axis=1)


# ── projection ───────────────────────────────────────────────────────────────────


def _project_segment(points: np.ndarray, corners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Closest point on the straight segment ``corners[0]-corners[1]``.

    ``(distance (N,), param (N, 3))``, the parametric coordinate in column 0 and the rest
    unused. A shell *edge* facet is one-dimensional, so this is the whole projection: a
    LINE3 edge's mid-side node bends the facet, and that curvature is treated exactly as
    :func:`_project_triangle` treats a TRI6's -- see :func:`nearest_facet`.
    """
    a, b = corners[0], corners[1]
    ab = b - a
    denom = float(ab @ ab)
    if denom == 0.0:
        t = np.zeros(points.shape[0])
    else:
        t = np.clip((points - a) @ ab / denom, 0.0, 1.0)
    closest = a + t[:, None] * ab
    param = np.zeros((points.shape[0], 3))
    param[:, 0] = 2.0 * t - 1.0
    return np.linalg.norm(points - closest, axis=1), param


def _project_triangle(points: np.ndarray, corners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Closest point on the triangle ``corners``, as ``(distance (N,), barycentric (N, 3))``.

    Four candidates, the smallest of which is the answer: the in-plane projection when it
    falls inside the triangle, and the closest point of each of the three edges (each already
    clamped, so the vertices are covered). That is exhaustive -- the closest point of a
    triangle is either interior or on its boundary -- and it branches only in the final
    ``argmin``, which is what keeps it vectorised over every point at once. The textbook
    region-by-region walk (Ericson) is cheaper per point and far worse here, because its
    seven-way branch cannot be expressed as one array expression.

    A degenerate (zero-area) triangle drops the interior candidate and is answered by its
    edges, which for a sliver is the correct answer anyway.
    """
    n = points.shape[0]
    v0 = corners[1] - corners[0]
    v1 = corners[2] - corners[0]
    v2 = points - corners[0]
    d00, d01, d11 = float(v0 @ v0), float(v0 @ v1), float(v1 @ v1)
    denom = d00 * d11 - d01 * d01

    dist = np.full((4, n), np.inf)
    bary = np.zeros((4, n, 3))

    if denom != 0.0:
        d20 = v2 @ v0
        d21 = v2 @ v1
        v = (d11 * d20 - d01 * d21) / denom
        w = (d00 * d21 - d01 * d20) / denom
        u = 1.0 - v - w
        inside = (u >= 0.0) & (v >= 0.0) & (w >= 0.0)
        closest = corners[0] + v[:, None] * v0 + w[:, None] * v1
        dist[0] = np.where(inside, np.linalg.norm(points - closest, axis=1), np.inf)
        bary[0, :, 0], bary[0, :, 1], bary[0, :, 2] = u, v, w

    for k, (i, j) in enumerate(((0, 1), (1, 2), (2, 0))):
        d, param = _project_segment(points, corners[[i, j]])
        dist[k + 1] = d
        t = (param[:, 0] + 1.0) / 2.0
        bary[k + 1, :, i] = 1.0 - t
        bary[k + 1, :, j] = t

    pick = dist.argmin(axis=0)
    rows = np.arange(n)
    return dist[pick, rows], bary[pick, rows]


def _project_quad(points: np.ndarray, corners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Closest point on a quadrilateral facet, as ``(distance (N,), (xi, eta) (N, 3))``.

    **A quadrilateral facet is in general not planar.** A QUAD8 face of a C3D20, or a QUAD
    shell, is a bilinear patch: its four corners need not be coplanar, and there is therefore
    no plane to drop a perpendicular onto. Projecting onto the patch itself means minimising
    a quartic, i.e. a Newton iteration per point per facet, which is both slow and -- for a
    strongly warped facet -- not reliably single-minimum.

    What is done instead: the quad is split along **both** diagonals into four corner
    triangles, each of which is planar, and the closest of the four is taken. The parametric
    coordinate comes back by mapping the winning triangle's barycentric coordinates onto the
    parametric coordinates of its three corners, which is affine and therefore stays inside
    the reference square. Using both diagonals rather than one keeps the answer independent of
    which corner the facet happens to be numbered from.

    Two things are given up, and only two. For a facet that is planar but not a
    parallelogram, the distance is *exact* -- the triangles cover the quadrilateral -- while
    the parametric coordinate is the affine preimage rather than the bilinear one, so the
    landing point is displaced tangentially by the facet's departure from affine. For a facet
    that is warped, the surface projected onto is piecewise flat rather than bilinear, so the
    distance is short of the true one by at most the warp and the landing point moves by the
    same order. Neither error reaches the constraint's kinematics. The weights are the facet's exact shape functions evaluated at the parametric
    point, so they still sum to 1 and still reproduce rigid-body motion of the main surface
    exactly; and the rigid arm written for each facet node runs to the secondary node's
    *actual* coordinates, not to the projection, so nothing inconsistent is written either.
    What the warp can change is which facet a node lands on when two are nearly equidistant,
    and the reported projection distance -- both by an amount smaller than the facet's own
    departure from flat, which for any mesh worth tying is far below the position tolerance
    the tie is judged by.

    The same reasoning covers a curved TRI6 or LINE3: the projection uses the corner
    geometry, the weights use the real shape functions.
    """
    n = points.shape[0]
    dist = np.full((len(_QUAD_TRIANGLES), n), np.inf)
    param = np.zeros((len(_QUAD_TRIANGLES), n, 3))
    for k, tri in enumerate(_QUAD_TRIANGLES):
        d, bary = _project_triangle(points, corners[list(tri)])
        dist[k] = d
        param[k, :, :2] = bary @ _QUAD_PARAM_CORNERS[list(tri)]
    pick = dist.argmin(axis=0)
    rows = np.arange(n)
    return dist[pick, rows], param[pick, rows]


@dataclass(frozen=True)
class _FacetKind:
    """How one facet topology is projected onto and interpolated over."""

    n_corners: int
    project: Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]
    weights: Callable[[np.ndarray], np.ndarray]


#: The facet topologies an interpolated tie can handle, keyed by ``SurfaceFacet.shape``.
#:
#: TRI7 / QUAD9 are deliberately absent. Their centre node makes them a different
#: interpolation, Abaqus has no such element, and quietly using the TRI6 / QUAD8 functions
#: over the first six or eight nodes would be an unreported approximation of a facet the
#: caller was told had been handled exactly. A facet whose shape is not in here is reported
#: and falls back to the nearest main node -- see :func:`nearest_facet`.
FACET_KINDS: dict[object, _FacetKind] = {
    LineShapes.LINE: _FacetKind(2, _project_segment, _line_weights),
    LineShapes.LINE3: _FacetKind(2, _project_segment, _line3_weights),
    ShellShapes.TRI: _FacetKind(3, _project_triangle, _tri_weights),
    ShellShapes.TRI6: _FacetKind(3, _project_triangle, _tri6_weights),
    ShellShapes.QUAD: _FacetKind(4, _project_quad, _quad_weights),
    ShellShapes.QUAD8: _FacetKind(4, _project_quad, _quad8_weights),
}


def facet_sort_key(facet: "SurfaceFacet") -> tuple[int, ...]:
    """A deterministic order for facets: their node ids, sorted.

    Two facets can be exactly equidistant from a secondary node -- a node on the shared edge
    of two faces is the common case, and 313 exact *node* distance ties were measured in a
    single real deck -- so which one wins must not depend on the order the region resolver
    happened to hand the facets back in. Sorting on node ids, and letting the first strict
    improvement win, makes the choice a property of the mesh.
    """
    return tuple(sorted(facet.node_ids))


@dataclass(frozen=True)
class FacetMatch:
    """Where each of N points lands on a set of facets.

    :param facet_index: index into the facet sequence *as passed in*, or -1 when the sequence
        was empty.
    :param distance: point-to-facet distance, ``inf`` where ``facet_index`` is -1. This is
        the measure Abaqus' position tolerance is defined against.
    :param weights: ``(N, max nodes per facet)``, row *i* holding the shape-function values of
        facet ``facet_index[i]`` at the projection, padded with zeros. Each row sums to 1 over
        that facet's own node count.
    """

    facet_index: np.ndarray
    distance: np.ndarray
    weights: np.ndarray


def nearest_facet(points: np.ndarray, facets: Sequence["SurfaceFacet"], max_distance: float) -> FacetMatch:
    """Project every point onto the closest of ``facets``, with that facet's weights there.

    Vectorised the way round that pays: the loop runs over facets (807 of them on the
    acceptance deck) and each iteration projects *every* point onto that one facet at once --
    7,357 points against 807 facets in 0.44 s. A loop over points instead is two orders of
    magnitude slower.

    ``max_distance`` is how far the caller cares to look. For a tie it is the position
    tolerance, since a point with no facet inside that is untied whichever facet is nearest;
    ``inf`` asks for the nearest facet unconditionally. A facet is only projected onto for the
    points its **bounding sphere** (centroid, and the distance from it to the farthest of the
    facet's nodes) cannot rule out, which is a valid lower bound because the projection always
    lands in the convex hull of the facet's corners. The radius is taken over *all* of the
    facet's nodes rather than its corners alone: the corner hull is the tighter bound and is
    correct for the projection as it stands, but the difference is only a mid-side node bowing
    outwards, so the looser bound costs a few extra candidate points and stays valid if the
    projection is ever made to follow the curved facet. The prune turns the expensive work from
    "every facet against every point" into "every facet against its own neighbourhood" and
    leaves only the sphere test quadratic, at about a fortieth of the cost per facet-point pair.
    Points left with no facet come back with ``facet_index`` -1 and an infinite distance.
    Without it, a tie over tens of thousands of facets and nodes is quadratic in the projection
    itself, which is minutes rather than seconds.

    Facets are visited in :func:`facet_sort_key` order and a facet must be **strictly** closer
    to take a point, so an exact tie goes to the facet with the lowest node ids. Equidistance
    is not a corner case: every node on the shared edge of two faces is equidistant from both.
    The pruning cannot disturb that: it only ever skips facets provably beyond
    ``max_distance``, and for a point with nothing inside the tolerance the choice is unused.

    Every facet must have an entry in :data:`FACET_KINDS`; one that does not raises rather
    than being passed over, because a facet quietly dropped is a piece of main surface the
    caller would go on believing it had projected onto. Filtering the ones that cannot be
    handled, and reporting them, belongs to the caller that owns the conversion report.
    """
    n = points.shape[0]
    facet_index = np.full(n, -1, dtype=np.int64)
    distance = np.full(n, np.inf)
    param = np.zeros((n, 3))

    order = sorted(range(len(facets)), key=lambda i: facet_sort_key(facets[i]))
    width = 0
    for fi in order:
        facet = facets[fi]
        kind = FACET_KINDS.get(facet.shape)
        if kind is None:
            raise ValueError(
                f"no shape functions for a {facet.shape} facet (element {facet.element.id}); "
                "filter the facets through FACET_KINDS and report the ones left out"
            )
        width = max(width, len(facet.nodes))
        facet_points = facet.points
        corners = facet_points[: kind.n_corners]

        centroid = facet_points.mean(axis=0)
        radius = float(np.linalg.norm(facet_points - centroid, axis=1).max())
        rows = np.flatnonzero(np.linalg.norm(points - centroid, axis=1) - radius <= max_distance)
        if rows.size == 0:
            continue

        d, p = kind.project(points[rows], corners)
        better = d < distance[rows]
        if not better.any():
            continue
        hit = rows[better]
        distance[hit] = d[better]
        facet_index[hit] = fi
        param[hit] = p[better]

    weights = np.zeros((n, max(width, 1)))
    for fi in np.unique(facet_index):
        if fi < 0:
            continue
        rows = facet_index == fi
        facet = facets[int(fi)]
        w = FACET_KINDS[facet.shape].weights(param[rows])
        weights[rows, : w.shape[1]] = w

    return FacetMatch(facet_index, distance, weights)
