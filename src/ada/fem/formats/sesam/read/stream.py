"""Single-pass *streaming* read of a Sesam .FEM deck's mesh.

The bulk of a .FEM file is GCOORD (nodes) and GELMNT1 (elements); everything else
(GNODE, GELREF1, section/material/set/BC cards) is comparatively tiny. This reader
iterates the file handle line-by-line (never holding the whole file as a string),
sends the mesh cards straight into packed numpy arrays, and buckets every other line
into a small text blob the existing object readers can still regex over.

Mirrors the streaming flag-dispatch that the Sesam SIF *results* reader
(``results/read_sif.py``) already uses — unifying the two paths on one model.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ada.fem import Elem
from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes.lines import SpringTypes


def _gelmnt_dict(vals: list[str]) -> dict:
    # vals are the GELMNT1 fields after the card name: elnox, elno, eltyp, eltyad, nids...
    return {
        "elnox": vals[0],
        "elno": vals[1],
        "eltyp": vals[2],
        "eltyad": vals[3],
        "nids": " ".join(vals[4:]),
    }


def stream_fem_mesh(path):
    """Stream a .FEM file once.

    Returns ``(coords (n,3), node_ids (n,), by_type, mass_elem, spring_elem, ext_map,
    other_text)`` where ``by_type`` maps a canonical element type to
    ``(el_ids, node-id conn)`` and ``other_text`` is every non-mesh line joined (small).
    """
    coord_ids: list[int] = []
    coord_xyz: list[tuple[float, float, float]] = []
    by_type: dict = defaultdict(lambda: ([], []))
    mass_elem: dict = {}
    spring_elem: dict = {}
    ext_map: dict = {}
    other: list[str] = []

    cur_card = None
    # current GELMNT1 being accumulated across continuation lines. A single .FEM
    # element record (structural, mass OR spring) can wrap onto continuation lines, so
    # we accumulate every field and dispatch on flush — never leak continuation node
    # ids into ``other``.
    cur_gelmnt = None  # (kind, el_type, el_no, vals)  kind in {"struct","mass","spring"}

    def flush_gelmnt():
        nonlocal cur_gelmnt
        if cur_gelmnt is None:
            return
        kind, el_type, el_no, vals = cur_gelmnt
        if kind == "struct":
            nids = [n for n in (str_to_int(x) for x in vals[4:]) if n != 0]
            ids, conns = by_type[el_type]
            ids.append(el_no)
            conns.append(nids)
        elif kind == "mass":
            mass_elem[el_no] = dict(gelmnt=_gelmnt_dict(vals))
        else:  # spring
            spring_elem[el_no] = dict(gelmnt=_gelmnt_dict(vals))
        cur_gelmnt = None

    with open(path, "r") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            first = s[0]

            if first.isdigit() or first == "-":
                # continuation line — belongs to the current card
                if cur_card == "GELMNT1" and cur_gelmnt is not None:
                    cur_gelmnt[3].extend(s.split())
                else:
                    other.append(line.rstrip("\n"))  # continuation of a non-mesh card
                continue

            tok = s.split(None, 1)[0]
            if cur_card == "GELMNT1" and tok != "GELMNT1":
                flush_gelmnt()
            cur_card = tok

            if tok == "GCOORD":
                p = s.split()
                coord_ids.append(str_to_int(p[1]))
                coord_xyz.append((float(p[2]), float(p[3]), float(p[4])))
            elif tok == "GELMNT1":
                flush_gelmnt()
                vals = s.split()[1:]
                el_no = str_to_int(vals[1])
                ext_map[el_no] = str_to_int(vals[0])
                el_type = sesam_eltype_2_general(str_to_int(vals[2]))
                if isinstance(el_type, SpringTypes):
                    cur_gelmnt = ("spring", el_type, el_no, vals)
                elif el_type == Elem.EL_TYPES.MASS_SHAPES.MASS:
                    cur_gelmnt = ("mass", el_type, el_no, vals)
                else:
                    cur_gelmnt = ("struct", el_type, el_no, vals)
            else:
                other.append(line.rstrip("\n"))

    flush_gelmnt()

    coords = np.array(coord_xyz, dtype=np.float64) if coord_xyz else np.zeros((0, 3))
    node_ids = np.array(coord_ids, dtype=np.int64) if coord_ids else np.zeros((0,), dtype=np.int64)
    return coords, node_ids, by_type, mass_elem, spring_elem, ext_map, "\n".join(other)
