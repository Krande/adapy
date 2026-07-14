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
