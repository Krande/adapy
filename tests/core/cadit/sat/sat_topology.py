"""Read a SESAM/ACIS SAT body back as topology, for asserting on writer output.

Entity numbering is positional and carries no meaning, so comparing writer output
to a Genie reference line-by-line asserts the wrong thing — it breaks on any
re-ordering while missing genuinely corrupt cross-references. These helpers walk
the record graph instead: :func:`ref_errors` checks every pointer resolves to the
right kind of entity, and :func:`digest` reports the body's shape (counts, each
face's boundary loop, sharing) independent of numbering.

Field layouts are per the ACIS SAT v4.0 spec: all records open with the ENTITY
prefix ``$attrib -1 -1 $owner`` at [0:4], then the entity's own fields at [4:].
"""

from __future__ import annotations

import re
from collections import Counter

# field index -> the entity type(s) that pointer must resolve to
_EXPECTED = {
    "body": {4: ("lump",), 5: ("wire",), 6: ("transform",)},
    "lump": {4: ("lump",), 5: ("shell",), 6: ("body",)},
    "shell": {4: ("shell",), 5: ("subshell",), 6: ("face",), 7: ("wire",), 8: ("lump",)},
    "face": {4: ("face",), 5: ("loop",), 6: ("shell",), 8: ("plane-surface", "spline-surface")},
    "loop": {4: ("loop",), 5: ("coedge",), 6: ("face",)},
    "coedge": {4: ("coedge",), 5: ("coedge",), 6: ("coedge",), 7: ("edge",), 9: ("loop", "wire")},
    "edge": {4: ("vertex",), 6: ("vertex",), 8: ("coedge",), 9: ("straight-curve",)},
    "vertex": {4: ("edge",), 5: ("point",)},
    "wire": {4: ("wire",), 5: ("coedge",), 6: ("body", "shell"), 7: ("subshell",)},
}


def parse(text: str) -> dict[int, tuple[str, list[str]]]:
    """{record index: (entity type, fields after the type token)}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^-(\d+)\s+(\S+)\s*(.*)$", line.strip())
        if m:
            out[int(m.group(1))] = (m.group(2), m.group(3).split())
    return out


def _ref(token: str) -> int | None:
    """The record a ``$n`` pointer names, or None for ``$-1`` / a non-pointer."""
    if not token.startswith("$"):
        return None
    idx = int(token[1:])
    return None if idx < 0 else idx


def ref_errors(text: str) -> list[str]:
    """Every pointer that dangles or resolves to the wrong entity type."""
    ents = parse(text)
    errors = []
    for idx, (etype, fields) in ents.items():
        for fi, expected in _EXPECTED.get(etype, {}).items():
            if fi >= len(fields):
                continue
            target = _ref(fields[fi])
            if target is None:
                continue
            if target not in ents:
                errors.append(f"-{idx} {etype}: field[{fi}]={fields[fi]} dangles")
            elif ents[target][0] not in expected:
                errors.append(f"-{idx} {etype}: field[{fi}]={fields[fi]} is {ents[target][0]!r}, expected {expected}")
    return errors


def wire_groups(text: str) -> dict:
    """How the wire (face-less) edges are grouped, and whether that is legal.

    ACIS wants every edge meeting at a vertex reachable within one group, so a
    connected run of edges has to live in a single wire; spread over several,
    the model fails verification with "vertex has edge in multiple groups".

    A wire coedge's ``next``/``prev`` are not a linear list — ``next`` is the
    following coedge around its END vertex and ``prev`` the one around its START
    vertex — so the coedges at each vertex form one closed ring, which is what
    keeps a branched wire walkable. Reports:

    ``wires``                      how many wire records
    ``free_edges``                 edges carried by a wire rather than a loop
    ``components``                 connected runs of those edges (shared vertex)
    ``wires_per_component``        {wires spanned: how many components} — must be {1: n}
    ``vertices_in_multiple_wires`` the failure itself; must be 0
    ``fans`` / ``fans_that_are_closed_rings``   must be equal
    """
    ents = parse(text)
    wires = {i for i, (t, _) in ents.items() if t == "wire"}
    coedges = {i: f for i, (t, f) in ents.items() if t == "coedge"}
    edges = {i: f for i, (t, f) in ents.items() if t == "edge"}
    mine = {i: f for i, f in coedges.items() if _ref(f[9]) in wires}

    def ends(cid):
        """(start, end) vertex of the coedge's edge, in the coedge's own sense."""
        e = edges[_ref(mine[cid][7])]
        a, b = _ref(e[4]), _ref(e[6])
        return (a, b) if mine[cid][8] == "forward" else (b, a)

    def around(cid, v):
        """The next coedge around vertex ``v``."""
        start, end = ends(cid)
        if v == end:
            return _ref(mine[cid][4])  # next
        if v == start:
            return _ref(mine[cid][5])  # prev
        return None

    wire_of_edge = {_ref(f[7]): _ref(f[9]) for f in mine.values()}

    parent = {e: e for e in wire_of_edge}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen_at = {}
    for e in wire_of_edge:
        for v in (_ref(edges[e][4]), _ref(edges[e][6])):
            if v in seen_at:
                ra, rb = find(seen_at[v]), find(e)
                if ra != rb:
                    parent[ra] = rb
            else:
                seen_at[v] = e
    comps = {}
    for e in wire_of_edge:
        comps.setdefault(find(e), []).append(e)

    fans = {}
    for cid in mine:
        start, end = ends(cid)
        for v in {start, end}:
            fans.setdefault(v, []).append(cid)

    closed = 0
    for v, cids in fans.items():
        first, cur, walked = cids[0], cids[0], []
        for _ in range(len(cids) + 1):
            walked.append(cur)
            cur = around(cur, v)
            if cur is None or cur == first:
                break
        if cur == first and set(walked) == set(cids):
            closed += 1

    wires_at_vertex = {}
    for e, w in wire_of_edge.items():
        for v in (_ref(edges[e][4]), _ref(edges[e][6])):
            wires_at_vertex.setdefault(v, set()).add(w)

    return {
        "wires": len(wires),
        "free_edges": len(wire_of_edge),
        "components": len(comps),
        "component_sizes": dict(sorted(Counter(len(c) for c in comps.values()).items())),
        "wires_per_component": dict(sorted(Counter(len({wire_of_edge[e] for e in c}) for c in comps.values()).items())),
        "vertices_in_multiple_wires": sum(1 for w in wires_at_vertex.values() if len(w) > 1),
        "fans": len(fans),
        "fans_that_are_closed_rings": closed,
    }


def partner_rings(text: str) -> dict:
    """Every edge's partner ring, and whether it runs in radial order.

    ACIS reads the ring of coedges on an edge as the angular order of the faces
    around it, so it has to be sorted; otherwise the model fails verification
    with "coedges out of order about edge". A coedge's face lies to the left of
    the direction it traverses the edge, so ``normal x tangent`` points from the
    edge into the face and its angle about the edge places that face radially.

    Returns ``{ring size: {"sorted_ccw": n, "sorted_cw": n, "unsorted": n}}``.
    Only sizes >= 2 appear; a 2-ring is trivially sorted whatever the order, so
    only 3+ tells you anything. Genie writes every ring counter-clockwise about
    the edge's own direction.
    """
    import math

    ents = parse(text)
    coedges = {i: f for i, (t, f) in ents.items() if t == "coedge"}
    loops = {i: f for i, (t, f) in ents.items() if t == "loop"}

    def vec(fields, i):
        return tuple(float(x) for x in fields[i : i + 3])

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    def unit(v):
        n = math.sqrt(sum(x * x for x in v))
        return None if n < 1e-12 else tuple(x / n for x in v)

    on_edge: dict[int, list[int]] = {}
    for cid, f in coedges.items():
        if _ref(f[9]) in loops:  # face coedges; wire coedges carry no partner
            on_edge.setdefault(_ref(f[7]), []).append(cid)

    def face_normal(cid):
        face = ents[_ref(loops[_ref(coedges[cid][9])][6])][1]
        n = vec(ents[_ref(face[8])][1], 7)
        return tuple(-x for x in n) if face[9] == "reversed" else n

    out: dict[int, Counter] = {}
    for eid, cids in on_edge.items():
        if len(cids) < 2:
            continue
        axis = unit(vec(ents[_ref(ents[eid][1][9])][1], 7))
        if axis is None:
            continue
        seed = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
        ref = unit(cross(axis, seed))
        ortho = cross(axis, ref)

        def angle(cid):
            tangent = axis if coedges[cid][8] == "forward" else tuple(-x for x in axis)
            into = unit(cross(face_normal(cid), tangent))
            if into is None:
                return None
            return math.atan2(sum(a * b for a, b in zip(into, ortho)), sum(a * b for a, b in zip(into, ref)))

        ring, cur = [], cids[0]
        for _ in range(len(cids) + 1):
            ring.append(cur)
            nxt = _ref(coedges[cur][6])
            if nxt is None or nxt == cids[0]:
                break
            cur = nxt
        bucket = out.setdefault(len(cids), Counter())
        if set(ring) != set(cids):
            bucket["ring_broken"] += 1
            continue
        angles = [angle(c) for c in ring]
        if any(a is None for a in angles):
            bucket["degenerate"] += 1
            continue

        def wraps_once(seq):
            steps = [(seq[(i + 1) % len(seq)] - seq[i]) % (2 * math.pi) for i in range(len(seq))]
            return all(s > 1e-9 for s in steps) and abs(sum(steps) - 2 * math.pi) < 1e-6

        if wraps_once(angles):
            bucket["sorted_ccw"] += 1
        elif wraps_once(angles[::-1]):
            bucket["sorted_cw"] += 1
        else:
            bucket["unsorted"] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def digest(text: str) -> dict:
    """Numbering-independent shape of the body.

    Walks shell -> face chain -> loop -> coedge ring, reporting each face's
    boundary as ordered points, its surface normal, and whether the loop winds
    counter-clockwise about that normal (``winding_dots`` > 0 — the two must
    agree or the face's material side is inverted).
    """
    ents = parse(text)
    counts = Counter(etype for etype, _ in ents.values())

    def point_of(vertex_idx: int) -> tuple[float, float, float]:
        point_idx = _ref(ents[vertex_idx][1][5])
        coords = ents[point_idx][1][4:7]
        return tuple(round(float(c), 9) for c in coords)

    body = next(i for i, (t, _) in ents.items() if t == "body")
    lump = _ref(ents[body][1][4])
    shell = _ref(ents[lump][1][5])

    boundaries, normals, dots = [], [], []
    face = _ref(ents[shell][1][6])
    seen = set()
    while face is not None and face not in seen:
        seen.add(face)
        fields = ents[face][1]

        loop = _ref(fields[5])
        first = _ref(ents[loop][1][5])
        pts, coedge = [], first
        while True:
            cfields = ents[coedge][1]
            efields = ents[_ref(cfields[7])][1]
            start, end = point_of(_ref(efields[4])), point_of(_ref(efields[6]))
            pts.append(end if cfields[8] == "reversed" else start)
            coedge = _ref(cfields[4])
            if coedge == first or coedge is None:
                break
        boundaries.append(pts)

        sfields = ents[_ref(fields[8])][1]
        normal = tuple(round(float(c), 6) for c in sfields[7:10])
        normals.append(normal)
        dots.append(round(sum(a * b for a, b in zip(_newell(pts), normal)), 6))

        face = _ref(fields[4])

    return {
        "counts": dict(counts),
        "faces_walked": len(boundaries),
        "boundaries": boundaries,
        "normals": normals,
        "winding_dots": dots,
        "coedges_with_partner": sum(1 for t, f in ents.values() if t == "coedge" and _ref(f[6]) is not None),
    }


def _newell(pts) -> tuple[float, float, float]:
    """Unit normal of a closed polygon, robust to non-convexity."""
    nx = ny = nz = 0.0
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    mag = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
    return (nx / mag, ny / mag, nz / mag)


def loop_as_cycle(pts) -> list:
    """A boundary loop normalised so equivalent loops compare equal.

    Two SAT writers can start the same loop at a different vertex; rotating to
    the lexicographically smallest start makes that irrelevant while keeping the
    traversal direction significant (which orientation depends on).
    """
    if not pts:
        return []
    start = min(range(len(pts)), key=lambda i: pts[i])
    return [pts[(start + i) % len(pts)] for i in range(len(pts))]
