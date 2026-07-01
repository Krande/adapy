from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.config import Config

from .exceptions import VectorNormalizeError

if TYPE_CHECKING:
    from ada import Placement, Point
    from ada.geom.direction import Direction


def angle_between(v1, v2):
    """
    Returns the angle in radians between vectors 'v1' and 'v2'

    source:

        https://stackoverflow.com/questions/2827393/angles-between-two-n-dimensional-vectors-in-python/13849249#13849249

    :param v1:
    :param v2:

            >>> angle_between((1, 0, 0), (0, 1, 0))
            1.5707963267948966
            >>> angle_between((1, 0, 0), (1, 0, 0))
            0.0
            >>> angle_between((1, 0, 0), (-1, 0, 0))
            3.141592653589793
    """
    v1_u = unit_vector(v1)
    v2_u = unit_vector(v2)
    return np.arccos(np.clip(np.dot(v1_u, v2_u), -1.0, 1.0))


def vector_length(vector: np.ndarray) -> float:
    """This method takes in a np.array vector and returns the length of the vector."""
    if vector.shape[0] != 3:
        if vector.shape[0] == 2:
            raise ValueError("Vector is not a 3d vector, but a 2d vector. Please consider vector_length_2d() instead")
        raise ValueError(f"Vector is not a 3d vector. Vector array length: {len(vector)}")

    return float(np.linalg.norm(vector))


def vector_length_2d(vector: np.ndarray) -> float:
    """This method takes in a np.array vector and returns the length of the vector."""
    if vector.shape[0] != 2:
        if vector.shape[0] == 3:
            raise ValueError("Vector is not a 2d vector, but a 3d vector. Please consider vector_length() instead")
        raise ValueError(f"Vector is not a 2d vector. Vector array length: {len(vector)}")

    return float(np.linalg.norm(vector))


def distfunc(x, point, A, B):
    """A function of x for the distance between point A on vector AB and arbitrary point C.
    X is a scalar multiplied with AB vector based on the distance to C."""
    AB = B - A
    return vector_length(point - (A + x * AB))


def sort_points_by_dist(p, points):
    return sorted(points, key=lambda x: vector_length(x - p))


def is_in_interval(value: float, interval_start: float, interval_end: float, incl_interval_ends: bool = False) -> bool:
    if incl_interval_ends:
        return interval_start <= value <= interval_end
    else:
        return interval_start < value < interval_end


def is_between_endpoints(p: np.ndarray, start: np.ndarray, end: np.ndarray, incl_endpoints: bool = False) -> bool:
    """Returns if point p is on the line between the points start and end"""
    if is_null_vector(p, start) or is_null_vector(p, end):
        if incl_endpoints:
            return True
        return False

    ab = end - start
    ap = p - start

    vec_fraction = get_vec_fraction(ap, ab)
    on_line_segment = is_in_interval(vec_fraction, 0.0, 1.0, incl_interval_ends=incl_endpoints)
    return is_parallel(ab, ap) and on_line_segment


def get_vec_fraction(vec: np.ndarray, reference_vec: np.ndarray) -> float:
    """Returns the fraction of the projection of vec onto reference_vec."""
    return np.dot(vec, reference_vec) / np.dot(reference_vec, reference_vec)


def point_on_line(start: np.ndarray, end: np.ndarray, point: np.ndarray) -> np.ndarray:
    ap = point - start
    ab = end - start
    result = start + get_vec_fraction(ap, ab) * ab
    return result


def is_null_vector(ab: np.array, cd: np.array, decimals=Config().general_precision) -> bool:
    """Check if difference in vectors AB and CD is null vector"""
    return np.array_equal((cd - ab).round(decimals), np.zeros_like(ab))


def is_parallel(ab: np.array, cd: np.array, tol=Config().general_point_tol) -> bool:
    """Check if vectors AB and CD are parallel"""
    return float(np.abs(np.sin(angle_between(ab, cd)))) < tol


def is_perpendicular(ab: np.array, cd: np.array, tol=Config().general_point_tol) -> bool:
    """Returns if the vectors are perpendicular"""
    return float(np.abs(np.dot(ab, cd))) < tol


def is_angled(vector_1: np.ndarray, vector_2: np.ndarray) -> bool:
    """Returns true if 2 vectors is not perpendicular nor parallel to each other"""
    return not (is_perpendicular(vector_1, vector_2) or is_parallel(vector_1, vector_2))


def intersect_calc(a: np.ndarray, c: np.ndarray, ab: np.ndarray, cd: np.ndarray):
    """Function for evaluating an intersection point between two vector-lines (AB & CD).  The function returns
    variables s & t denoting the scalar value multiplied with the two vector equations A + s*AB = C + t*CD."""
    # Setting up the equation for use in linalg.lstsq
    matrix = np.array((ab, -cd)).T
    vec = c - a

    st = np.linalg.lstsq(matrix, vec, rcond=None)

    s = st[0][0]
    t = st[0][1]
    return s, t


def intersection_point(v1, v2):
    """Get the coordinate of the intersecting point between vectors v1 and v2"""
    if isinstance(v1, np.ndarray):
        is2d = v1.shape[0] == 2
    else:
        is2d = len(list(v1[0])) == 2

    v1 = [np.array(list(v) + [0.0]) for v in list(v1)] if is2d else v1
    v2 = [np.array(list(v) + [0.0]) for v in list(v2)] if is2d else v2
    v1_ = v1[1] - v1[0]
    v2_ = v2[1] - v2[0]
    p1 = v1[0]
    p3 = v2[0]
    s, t = intersect_calc(p1, p3, v1_, v2_)
    res = p1 + s * v1_
    if is2d:
        return res[0], res[1]
    else:
        return res


def points_in_cylinder(start: np.ndarray, end: np.ndarray, radius, point: np.ndarray):
    """Check if point is inside a cylinder with start and end points as the cylinder axis and radius as the radius of
    the cylinder."""
    vec = end - start
    const = radius * np.linalg.norm(vec)
    if (
        np.dot(point - start, vec) >= 0 >= np.dot(point - end, vec)
        and np.linalg.norm(np.cross(point - start, vec)) <= const
    ):
        return True
    else:
        return False


def split(u, v, points):
    """

    :param u: Vector 1
    :param v: Vector 2
    :param points:
    :return:
    """

    # return points on left side of UV
    return [p for p in points if np.cross(p - u, v - u) < 0]


def extend(u, v, points):
    if not points:
        return []

    # find furthest point W, and split search to WV, UW
    w = min(points, key=lambda p: np.cross(p - u, v - u))
    p1, p2 = split(w, v, points), split(u, w, points)
    return extend(w, v, p1) + [w] + extend(u, w, p2)


def convex_hull(points):
    # find two hull points, U, V, and split to left and right search
    u = min(points, key=lambda p: p[0])
    v = max(points, key=lambda p: p[0])
    left, right = split(u, v, points), split(v, u, points)

    # find convex hull on each side
    return [v] + extend(u, v, left) + [u] + extend(v, u, right) + [v]


def is_coplanar(x1, y1, z1, x2, y2, z2, x3, y3, z3, x4, y4, z4) -> bool:
    """Python program to check if 4 points in a 3-D plane are Coplanar Function to find equation of plane."""
    a1 = x2 - x1
    b1 = y2 - y1
    c1 = z2 - z1
    a2 = x3 - x1
    b2 = y3 - y1
    c2 = z3 - z1
    a = b1 * c2 - b2 * c1
    b = a2 * c1 - a1 * c2
    c = a1 * b2 - b1 * a2
    d = -a * x1 - b * y1 - c * z1
    # equation of plane is: a*x + b*y + c*z = 0 #
    # checking if the 4th point satisfies
    # the above equation
    if a * x4 + b * y4 + c * z4 + d == 0:
        # if(a * x + b * y + c * z + d < 0.0001 and a * x + b * y + c * z + d > -0.0001):
        # print("Coplanar")
        return True
    else:
        # print(a * x + b * y + c * z + d)
        return False
        # print("Not Coplanar")


def poly_area(x, y):
    """
    Shoelace formula based on stackoverflow

    https://stackoverflow.com/questions/24467972/calculate-area-of-polygon-given-x-y-coordinates

    :param x:
    :param y:
    :return:
    """
    return 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def poly_area_from_list(coords):
    return poly_area(*zip(*coords))


def poly2d_center_of_gravity(polygon):
    """Calculates the center of gravity of a polygon described by a numpy array containing x,y,z coordinates."""
    sum_area = 0
    sum_cx = 0
    sum_cy = 0

    for i in range(len(polygon)):
        j = (i + 1) % len(polygon)
        area = polygon[i][0] * polygon[j][1] - polygon[j][0] * polygon[i][1]
        sum_area += area
        sum_cx += (polygon[i][0] + polygon[j][0]) * area
        sum_cy += (polygon[i][1] + polygon[j][1]) * area

    if sum_area == 0:
        return None

    cx = sum_cx / (3 * sum_area)
    cy = sum_cy / (3 * sum_area)

    return np.array([cx, cy])


# internal helper that works on a hashable tuple
@lru_cache(maxsize=1024)
def _unit_vector_cached(vec_tup: tuple[float, ...]) -> tuple[float, ...]:
    arr = np.array(vec_tup, dtype=float)
    norm_arr = arr / np.linalg.norm(arr)
    if np.isnan(norm_arr).any():
        raise VectorNormalizeError(f'Error trying to normalize vector "{arr}"')
    return tuple(norm_arr)


def unit_vector(vector: np.ndarray | list | tuple) -> Direction:
    """Returns the unit vector of a given vector, with LRU caching."""
    # turn the array into a tuple key
    if isinstance(vector, np.ndarray):
        vec_tup = tuple(vector.tolist())
    else:
        vec_tup = tuple(vector)
    norm_tup = _unit_vector_cached(vec_tup)
    # expand back into Direction. Imported lazily: a module-level
    # ``from ada.geom.direction import Direction`` creates a core↔geom
    # import cycle (geom → curve_utils → vector_utils → geom) that breaks
    # whenever ada.api.* is the first entry into the cluster (e.g. the
    # pyodide slim init). geom is the more fundamental layer, so core
    # resolves Direction on demand instead.
    from ada.geom.direction import Direction

    return Direction(*norm_tup)


def is_clockwise(points) -> bool:
    """Return true if order of 2d points are sorted in a clockwise order"""
    if isinstance(points, np.ndarray):
        points = points.copy()
    psum = 0
    for p1, p2 in zip(points[:-1], points[1:]):
        psum += (p2[0] - p1[0]) * (p2[1] + p1[1])
    psum += (points[-1][0] - points[0][0]) * (points[-1][1] + points[0][1])
    return not float(psum) < 0


def is_on_line(data):
    """Evaluate intersection point between two lines"""
    l, bm = data
    A, B = np.array(l[0]), np.array(l[1])
    AB = B - A
    C = bm.n1.p
    D = bm.n2.p
    CD = D - C

    if (vector_length(A - C) < 1e-5) is True and (vector_length(B - D) < 1e-5) is True:
        return None

    s, t = intersect_calc(A, C, AB, CD)
    AB_ = A + s * AB
    CD_ = C + t * CD
    if (vector_length(AB_ - CD_) < 1e-4) is True and s not in (0.0, 1.0):
        return list(AB_), bm
    else:
        return None


def create_right_hand_vectors_xv_yv_from_zv(z_vector: Iterable) -> tuple[Direction, Direction]:
    from ada.geom.placement import Direction

    # Ensure the z_vector is a numpy array
    if isinstance(z_vector, Direction) is False:
        z_vector = Direction(*z_vector)

    # Normalize z_vector
    z_vector = z_vector / np.linalg.norm(z_vector)

    # Check if z_vector is (0, 0, 0)
    if np.all(z_vector == 0):
        raise ValueError("Input vector cannot be the zero vector")

    # Create an arbitrary x_vector not parallel to z_vector
    if is_parallel(z_vector, np.array([1, 0, 0])):
        x_vector = np.array([0, 1, 0])
    else:
        x_vector = np.array([1, 0, 0])

    # Calculate the y_vector using the cross product
    y_vector = fast_cross(z_vector, x_vector)
    y_vector = y_vector / np.linalg.norm(y_vector)  # normalize y_vector

    # Adjust x_vector by recalculating the cross product of y_vector and z_vector for right-handedness
    x_vector = fast_cross(y_vector, z_vector)
    x_vector = x_vector / np.linalg.norm(x_vector)  # normalize x_vector

    return Direction(*x_vector), Direction(*y_vector)


def fast_cross(a, b) -> np.ndarray:
    """Cross product of two length-3 vectors without ``np.cross`` overhead.

    ``np.cross`` spends most of its time in ``moveaxis`` / ``normalize_axis_tuple``
    bookkeeping that is pure waste for fixed length-3 inputs. Authoring the three
    components by hand is several times faster and is the dominant per-object cost
    in the Genie/SAT plate-read placement path.
    """
    return np.array(
        (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )
    )


def calc_xvec(y_vec, z_vec) -> np.ndarray:
    return fast_cross(y_vec, z_vec)


def calc_yvec(x_vec, z_vec=None) -> np.ndarray:
    if z_vec is None:
        calc_zvec(x_vec)

    return fast_cross(z_vec, x_vec)


def calc_zvec(x_vec, y_vec=None) -> np.ndarray:
    """Calculate Z-vector (up) from an x-vector (along beam) only following a right handed coordinate system."""
    from ada.core.constants import Y, Z

    if y_vec is None:
        z_vec = np.array(Z)
        a = angle_between(x_vec, z_vec)
        if a == np.pi or a == 0:
            z_vec = np.array(Y)
        return z_vec
    else:
        return fast_cross(x_vec, y_vec)


def get_centroid(points) -> Point:
    from ada import Point

    x, y, z = 0, 0, 0
    for p in points:
        x += p[0]
        y += p[1]
        z += p[2]

    n = len(points)

    return Point(x / n, y / n, z / n)


Point3 = tuple[float, float, float]


def is_coplanar_points(points, tol: float = 1e-6) -> bool:
    """Return True if all 3D points lie on a common plane within ``tol``.

    Unlike :func:`is_coplanar`, which only accepts exactly four points and uses
    exact equality, this accepts any number of points and uses a tolerance.
    """
    unique: list[tuple[float, float, float]] = []
    seen: set[tuple[float, float, float]] = set()
    for p in points:
        key = (round(float(p[0]), 9), round(float(p[1]), 9), round(float(p[2]), 9))
        if key not in seen:
            seen.add(key)
            unique.append((float(p[0]), float(p[1]), float(p[2])))

    if len(unique) < 4:
        return True

    p0 = unique[0]
    v1 = None
    v2 = None
    tol_sq = tol * tol

    for i in range(1, len(unique)):
        va = (unique[i][0] - p0[0], unique[i][1] - p0[1], unique[i][2] - p0[2])
        if va[0] * va[0] + va[1] * va[1] + va[2] * va[2] <= tol_sq:
            continue
        for j in range(i + 1, len(unique)):
            vb = (unique[j][0] - p0[0], unique[j][1] - p0[1], unique[j][2] - p0[2])
            cross = (
                va[1] * vb[2] - va[2] * vb[1],
                va[2] * vb[0] - va[0] * vb[2],
                va[0] * vb[1] - va[1] * vb[0],
            )
            if cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2] > tol_sq:
                v1, v2 = va, vb
                break
        if v1 is not None:
            break

    if v1 is None:
        return False

    normal = (
        v1[1] * v2[2] - v1[2] * v2[1],
        v1[2] * v2[0] - v1[0] * v2[2],
        v1[0] * v2[1] - v1[1] * v2[0],
    )
    nlen = (normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2) ** 0.5
    if nlen <= tol:
        return False
    nx, ny, nz = normal[0] / nlen, normal[1] / nlen, normal[2] / nlen

    for p in unique:
        dx, dy, dz = p[0] - p0[0], p[1] - p0[1], p[2] - p0[2]
        if abs(dx * nx + dy * ny + dz * nz) > tol:
            return False
    return True


def project_points_to_local_2d(points3d) -> tuple[list[tuple[float, float]], Placement]:
    """Project coplanar 3D points to their best-fit local 2D frame.

    Returns ``(pts2d, placement)``. Use ``placement`` to round-trip back to 3D.
    """
    from ada.api.transforms import Placement

    arr = np.asarray(points3d, dtype=float)
    place = Placement.from_co_linear_points(arr)
    pts2d = place.transform_global_points_to_local(arr)
    return [(float(p[0]), float(p[1])) for p in pts2d], place


def _polygon_scale_2d(pts2d) -> float:
    xs = [p[0] for p in pts2d]
    ys = [p[1] for p in pts2d]
    return max(max(xs) - min(xs), max(ys) - min(ys), 1.0)


def remove_near_collinear_points(points3d, tol_factor: float = 1e-8):
    """Drop vertices of a closed planar polygon that are (near-)collinear with their neighbors.

    Uses a best-fit plane to project to 2D before measuring the triangle area,
    so it works for polygons on arbitrarily-oriented planes.
    """
    pts = list(points3d)
    if len(pts) < 4:
        return pts

    try:
        pts2d, _ = project_points_to_local_2d(pts)
    except Exception:
        return pts

    scale = _polygon_scale_2d(pts2d)
    tol = tol_factor * scale * scale

    cleaned = []
    n = len(pts)
    for i in range(n):
        a = pts2d[i - 1]
        b = pts2d[i]
        c = pts2d[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > tol:
            cleaned.append(pts[i])

    return cleaned if len(cleaned) >= 3 else pts


def has_reflex_vertex(pts2d, tol: float = 1e-9) -> bool:
    """Return True if the closed 2D polygon has at least one reflex (non-convex) vertex."""
    n = len(pts2d)
    if n < 4:
        return False

    area = 0.0
    for i in range(n):
        x1, y1 = pts2d[i]
        x2, y2 = pts2d[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    area *= 0.5
    if abs(area) < tol:
        return False

    orientation = 1.0 if area > 0.0 else -1.0
    for i in range(n):
        ax, ay = pts2d[i - 1]
        bx, by = pts2d[i]
        cx, cy = pts2d[(i + 1) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross * orientation < -tol:
            return True
    return False


def boundary_cycles_3d(facet_loops, ndigits: int = 9):
    """Oriented boundary loops of a set of facet loops as raw 3D point rings — the
    surface-agnostic core of :func:`extract_boundary_loops` (no planar projection), for
    boundaries that live on a curved surface (e.g. a cylinder's two end cuts). Facets are
    oriented consistently (BFS flip over shared edges); the net directed boundary is traced
    into cycles. Returns ``list[list[point3d]]`` or None on a non-manifold pinch (degree>2).
    """
    from collections import defaultdict, deque

    def _rnd(p):
        return (round(float(p[0]), ndigits), round(float(p[1]), ndigits), round(float(p[2]), ndigits))

    facets: list = []
    prep: dict = {}
    for pts in facet_loops:
        pts = list(pts)
        if len(pts) >= 2 and _rnd(pts[0]) == _rnd(pts[-1]):
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        ring = [_rnd(p) for p in pts]
        for p, k in zip(pts, ring):
            prep.setdefault(k, p)
        facets.append(ring)
    if not facets:
        return None

    owners: dict = defaultdict(list)
    for fi, f in enumerate(facets):
        m = len(f)
        for i in range(m):
            a, b = f[i], f[(i + 1) % m]
            owners[(a, b) if a <= b else (b, a)].append((fi, a, b))
    adj: dict = defaultdict(list)
    for lst in owners.values():
        if len(lst) == 2:
            (f0, a0, b0), (f1, a1, b1) = lst
            adj[f0].append((f1, (a0, b0) == (a1, b1)))
            adj[f1].append((f0, (a0, b0) == (a1, b1)))
    flip: list = [None] * len(facets)
    for s in range(len(facets)):
        if flip[s] is not None:
            continue
        flip[s] = False
        q = deque([s])
        while q:
            c = q.popleft()
            for nb, same in adj[c]:
                if flip[nb] is None:
                    flip[nb] = flip[c] ^ same
                    q.append(nb)

    dc: dict = defaultdict(int)
    for fi, f in enumerate(facets):
        ring = f[::-1] if flip[fi] else f
        m = len(ring)
        for i in range(m):
            dc[(ring[i], ring[(i + 1) % m])] += 1
    out_star: dict = defaultdict(list)
    total = 0
    for (a, b), c in dc.items():
        for _ in range(max(0, c - dc.get((b, a), 0))):
            out_star[a].append(b)
            total += 1
    if total < 3:
        return None

    cycles: list = []
    for sa in list(out_star):
        while out_star[sa]:
            loop = [sa]
            cur = out_star[sa].pop()
            guard = 0
            while cur != sa:
                loop.append(cur)
                nxt = out_star.get(cur)
                if not nxt or len(nxt) > 1:  # dangling or pinch (degree>2) — caller falls back
                    return None
                cur = nxt.pop()
                guard += 1
                if guard > total + 5:
                    return None
            cycles.append([prep[k] for k in loop])
    return cycles or None


def extract_boundary_loops(facet_loops, ndigits: int = 9):
    """Robust boundary of a set of (near-)coplanar facet loops → a list of faces
    ``[(outer, holes), ...]`` (each ``outer`` a CCW loop of 3D points, ``holes`` its CW
    inner loops), or None if it can't be resolved.

    Unlike :func:`merge_coplanar_loops_by_edge_cancellation` (single clean loop or None)
    this recovers cut-outs (→ faces with voids) AND non-manifold pinches: facets are
    oriented consistently (BFS flip over shared edges) so interior edges cancel and the
    net directed boundary forms the region's oriented cycles; at a pinch vertex (degree>2)
    the next edge is chosen by angle (hug the interior-on-left boundary), splitting the
    region into its separate simple loops. Positive-area loops are outer boundaries (a
    pinched region yields several), negative-area loops are holes, each assigned to its
    smallest containing outer.
    """
    import math

    import numpy as np

    def _rnd(p):
        return (round(float(p[0]), ndigits), round(float(p[1]), ndigits), round(float(p[2]), ndigits))

    facets: list = []
    prep: dict = {}
    for pts in facet_loops:
        pts = list(pts)
        if len(pts) >= 2 and _rnd(pts[0]) == _rnd(pts[-1]):
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        ring = [_rnd(p) for p in pts]
        for p, k in zip(pts, ring):
            prep.setdefault(k, p)
        facets.append(ring)
    if not facets:
        return None

    # ── 1. orient facets consistently (material on a common side) ──
    from collections import defaultdict, deque

    owners: dict = defaultdict(list)  # undirected edge -> [(facet_idx, a, b), ...]
    for fi, f in enumerate(facets):
        m = len(f)
        for i in range(m):
            a, b = f[i], f[(i + 1) % m]
            owners[(a, b) if a <= b else (b, a)].append((fi, a, b))
    adj: dict = defaultdict(list)
    for lst in owners.values():
        if len(lst) == 2:  # a manifold interior edge shared by two facets
            (f0, a0, b0), (f1, a1, b1) = lst
            same = (a0, b0) == (a1, b1)  # same direction in both rings => inconsistent winding
            adj[f0].append((f1, same))
            adj[f1].append((f0, same))
    flip: list = [None] * len(facets)
    for s in range(len(facets)):
        if flip[s] is not None:
            continue
        flip[s] = False
        q = deque([s])
        while q:
            c = q.popleft()
            for nb, same in adj[c]:
                want = flip[c] ^ same  # flip neighbour iff it traverses the shared edge the same way
                if flip[nb] is None:
                    flip[nb] = want
                    q.append(nb)

    # ── 2. net directed boundary edges (interior cancels) ──
    dc: dict = defaultdict(int)
    for fi, f in enumerate(facets):
        ring = f[::-1] if flip[fi] else f
        m = len(ring)
        for i in range(m):
            dc[(ring[i], ring[(i + 1) % m])] += 1
    out_star: dict = defaultdict(list)
    n_boundary = 0
    for (a, b), c in dc.items():
        net = c - dc.get((b, a), 0)
        for _ in range(max(0, net)):
            out_star[a].append(b)
            n_boundary += 1
    if n_boundary < 3:
        return None

    # ── 3. plane frame (for angular tie-breaks + signed area) ──
    keys = list({k for ws in out_star.values() for k in ws} | set(out_star))
    Pk = np.array([prep[k] for k in keys])
    ctr = Pk.mean(axis=0)
    _, _, vt = np.linalg.svd(Pk - ctr, full_matrices=False)
    e1, e2 = vt[0], vt[1]
    xy = {k: (float((np.array(prep[k]) - ctr) @ e1), float((np.array(prep[k]) - ctr) @ e2)) for k in keys}
    two_pi = 2.0 * math.pi

    def _ang(a, b):
        return math.atan2(xy[b][1] - xy[a][1], xy[b][0] - xy[a][0])

    # ── 4. trace oriented cycles; at a pinch pick the interior-on-left next edge ──
    out_map: dict = {v: list(ws) for v, ws in out_star.items()}
    avail: dict = defaultdict(int)
    for v, ws in out_map.items():
        for w in ws:
            avail[(v, w)] += 1
    loops_keys: list = []
    for sa in list(out_map):
        for sb in list(out_map[sa]):
            while avail[(sa, sb)] > 0:
                avail[(sa, sb)] -= 1
                loop = [sa]
                prev, cur = sa, sb
                guard = 0
                while cur != sa:
                    loop.append(cur)
                    cands = [w for w in out_map.get(cur, ()) if avail[(cur, w)] > 0]
                    if not cands:
                        return None
                    if len(cands) == 1:
                        w = cands[0]
                    else:  # pinch: hug the interior-on-left boundary — largest CCW turn from
                        # the reverse edge (sharpest right turn in traversal), never the U-turn back.
                        a_rev = _ang(cur, prev)
                        w = max(cands, key=lambda x: (_ang(cur, x) - a_rev) % two_pi)
                    avail[(cur, w)] -= 1
                    prev, cur = cur, w
                    guard += 1
                    if guard > n_boundary + 5:
                        return None
                loops_keys.append(loop)
    if not loops_keys:
        return None

    # ── 5. classify: +area = outer boundary, -area = hole; assign holes to containers ──
    def _signed_area(lp):
        s = 0.0
        for i in range(len(lp)):
            x1, y1 = xy[lp[i]]
            x2, y2 = xy[lp[(i + 1) % len(lp)]]
            s += x1 * y2 - x2 * y1
        return 0.5 * s

    def _centroid(lp):
        xs = [xy[k][0] for k in lp]
        ys = [xy[k][1] for k in lp]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def _contains(lp, pt):  # ray-cast point-in-polygon in the plane
        px, py = pt
        inside = False
        n = len(lp)
        for i in range(n):
            x1, y1 = xy[lp[i]]
            x2, y2 = xy[lp[(i + 1) % n]]
            if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1 + 1e-30) + x1:
                inside = not inside
        return inside

    scored: list = []  # (signed_area, loop_keys, pts3)
    for lp in loops_keys:
        pts3 = remove_near_collinear_points([prep[k] for k in lp])
        if len(pts3) < 3:
            continue
        scored.append((_signed_area(lp), lp, pts3))
    if not scored:
        return None
    # The plane frame's handedness (SVD e1×e2 sign) is arbitrary, so "positive area" is not
    # reliably the outer — for ~half the patches every loop comes out negative. Anchor the
    # global orientation on the largest-|area| loop (always an outer boundary): flip all signs
    # so it reads positive, then +area = outer, -area = hole. Without this, a whole flat patch
    # whose outer happened to trace CW returned None and shattered to per-facet.
    if max(scored, key=lambda s: abs(s[0]))[0] < 0:
        scored = [(-a, lp, p) for (a, lp, p) in scored]
    outers: list = [(abs(a), lp, p) for (a, lp, p) in scored if a > 0]
    holes: list = [(abs(a), lp, p) for (a, lp, p) in scored if a < 0]
    if not outers:
        return None
    faces = [(pts3, []) for (_a, _lp, pts3) in outers]
    for _ha, hlp, hp in holes:
        hc = _centroid(hlp)
        best = None
        for oi, (oa, olp, _op) in enumerate(outers):
            if _contains(olp, hc) and (best is None or oa < outers[best][0]):
                best = oi
        if best is not None:
            faces[best][1].append(hp)
    return faces


def merge_coplanar_loops_by_edge_cancellation(loops, ndigits: int = 9):
    """Merge coplanar planar polygon loops by canceling edges shared between them.

    Each input loop is a list of 3D points (not repeating the first point).
    Returns a single outer loop as 3D points, or ``None`` if the inputs do not
    form a single topologically clean outer boundary (e.g. they produce a hole,
    leave a non-manifold vertex, or split into multiple loops).
    """

    def _round(pt):
        return (round(float(pt[0]), ndigits), round(float(pt[1]), ndigits), round(float(pt[2]), ndigits))

    def _edge_key(a, b):
        return (a, b) if a <= b else (b, a)

    edge_counts: dict = {}
    point_repr: dict = {}
    any_valid = False

    for pts in loops:
        pts = list(pts)
        if len(pts) >= 2 and _round(pts[0]) == _round(pts[-1]):
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        any_valid = True

        n = len(pts)
        for i in range(n):
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            k1, k2 = _round(p1), _round(p2)
            if k1 == k2:
                continue
            key = _edge_key(k1, k2)
            edge_counts[key] = edge_counts.get(key, 0) + 1
            point_repr.setdefault(k1, p1)
            point_repr.setdefault(k2, p2)

    if not any_valid:
        return None

    boundary = [k for k, c in edge_counts.items() if c == 1]
    if len(boundary) < 3:
        return None

    adjacency: dict = {}
    for k1, k2 in boundary:
        adjacency.setdefault(k1, []).append(k2)
        adjacency.setdefault(k2, []).append(k1)

    if any(len(neigh) != 2 for neigh in adjacency.values()):
        return None

    unused = set(boundary)
    loops_found = []
    while unused:
        start = next(iter(unused))[0]
        loop = [start]
        current = start
        prev = None
        while True:
            neigh = adjacency[current]
            nxt = neigh[0] if prev is None or neigh[0] != prev else neigh[1]
            key = _edge_key(current, nxt)
            if key not in unused:
                return None
            unused.remove(key)
            prev, current = current, nxt
            if current == start:
                break
            loop.append(current)
        loops_found.append(loop)

    if len(loops_found) != 1:
        return None

    merged = [point_repr[k] for k in loops_found[0]]
    merged = remove_near_collinear_points(merged)
    if len(merged) < 3:
        return None
    return merged


class FastSegment:
    __slots__ = ("x0", "y0", "z0", "vx", "vy", "vz", "len2")

    def __init__(self, start: Point3, end: Point3) -> None:
        self.x0, self.y0, self.z0 = start
        ex, ey, ez = end
        self.vx = ex - self.x0
        self.vy = ey - self.y0
        self.vz = ez - self.z0
        self.len2 = self.vx * self.vx + self.vy * self.vy + self.vz * self.vz

    def contains(self, pt: Point3, tol: float = 1e-6, include_ends: bool = True) -> bool:
        dx = pt[0] - self.x0
        dy = pt[1] - self.y0
        dz = pt[2] - self.z0

        # cross product components
        c1 = dy * self.vz - dz * self.vy
        c2 = dz * self.vx - dx * self.vz
        c3 = dx * self.vy - dy * self.vx

        # squared‐norm check
        if (c1 * c1 + c2 * c2 + c3 * c3) > tol * tol:
            return False

        # dot‐product
        dot = dx * self.vx + dy * self.vy + dz * self.vz
        if include_ends:
            return -tol <= dot <= self.len2 + tol
        else:
            return tol < dot < self.len2 - tol
