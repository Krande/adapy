"""Springs: ``*Element, type=SPRING1/SPRING2`` with their ``*Spring``, and linear user elements.

An adapy :class:`~ada.fem.Spring` is one element carrying a whole stiffness matrix. Abaqus has
no such element. A ``SPRING1``/``SPRING2`` element has ONE stiffness: the ``*Spring`` on its
element set gives the DOF (the DOF pair for ``SPRING2``) and the value. So the writer writes a
spring as one such element per non-zero term, each in an element set of its own. A spring
whose DOFs are coupled (a grounded 6x6 from a Sesam ``MGSPRNG``) has no SPRING1 form. The
writer writes it as a linear user element instead: ``*User Element, linear`` plus
``*Matrix, type=STIFFNESS``, which holds the full matrix exactly.

Every element the writer emits for one spring sits under a ``** Spring: <name>  Nset: <set>``
comment. The reader groups them back into that spring, under the id of the first. A deck from
anywhere else has no such comment; each of its spring elements reads as a spring of its own,
named after its element set.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np

from ada.fem import FemSet, Spring
from ada.fem.formats.utils import str_to_int

from .keywords import validate
from .lexer import KeywordBlock, comment_property, iter_keywords

if TYPE_CHECKING:
    from ada import FEM

SPRING_TYPES = ("SPRING1", "SPRING2")
_USER_TYPE = re.compile(r"^V?U\d+$", re.IGNORECASE)
_STAGE = "abaqus reader"


def is_spring_block(eltype: str | None) -> bool:
    """Element blocks this module reads -- the element readers leave them alone."""
    return eltype is not None and (eltype.upper() in SPRING_TYPES or _USER_TYPE.match(eltype) is not None)


def _floats(lines) -> list[float]:
    return [float(t) for line in lines for t in line.split(",") if t.strip()]


def _report():
    from ada.fem.formats import conversion_report

    return conversion_report.current()


def _linear_user_elements(bulk_str: str) -> dict[str, tuple[int, list[int], np.ndarray]]:
    """``{type: (nodes, active dofs, stiffness)}`` for each ``*User Element, linear`` that has a
    symmetric ``*Matrix, type=STIFFNESS`` (given as its lower triangle, row by row)."""
    out = {}
    current = None
    for block in iter_keywords(bulk_str, "USER ELEMENT", "MATRIX"):
        validate(block)
        if block.keyword == "USER ELEMENT":
            current = None
            if "LINEAR" in block.params and "UNSYMM" not in block.params and "FILE" not in block.params:
                dofs = [int(t) for t in block.data_lines[0].split(",") if t.strip()] if block.data_lines else []
                current = (block.params.get("TYPE").upper(), int(block.params.get("NODES")), dofs)
            continue
        if current is None or (block.params.get("TYPE") or "").upper() != "STIFFNESS":
            continue
        name, nodes, dofs = current
        n = nodes * len(dofs)
        values = _floats(block.data_lines)
        if len(values) != n * (n + 1) // 2:
            continue
        k = np.zeros((n, n))
        k[np.tril_indices(n)] = values
        out[name] = (nodes, dofs, k + k.T - np.diag(np.diag(k)))
    return out


def _behaviours(bulk_str: str) -> dict[str, tuple[tuple[int, ...], float]]:
    """``{elset (lower case): (dofs, stiffness)}`` for each linear ``*Spring``."""
    out = {}
    for block in iter_keywords(bulk_str, "SPRING"):
        validate(block)
        elset = block.params.get("ELSET")
        if elset is None:  # an ITS / JOINTC behaviour, not a spring element's
            continue
        unsupported = [p for p in ("NONLINEAR", "ORIENTATION", "DEPENDENCIES", "COMPLEX STIFFNESS") if p in block.params]
        lines = block.data_lines
        if unsupported or len(lines) != 2:
            reason = f"{', '.join(unsupported)} not read" if unsupported else "a table of stiffnesses is not read"
            _report().omitted(_STAGE, "*SPRING", elset, reason)
            continue
        dofs = tuple(int(t) for t in lines[0].split(",") if t.strip())
        out[elset.lower()] = (dofs, _floats(lines[1:2])[0])
    return out


def get_springs_from_bulk(bulk_str: str, fem: FEM) -> list[Spring]:
    user_elements = _linear_user_elements(bulk_str)
    behaviours = _behaviours(bulk_str)

    # name -> [el id, nodes, el type, stiffness, nset name]; insertion order is deck order.
    springs: dict[str, list] = {}
    rows_per_elset: dict[str, int] = defaultdict(int)
    blocks: list[tuple[KeywordBlock, list[tuple[int, list]]]] = []
    for block in iter_keywords(bulk_str, "ELEMENT"):
        eltype = block.params.get("TYPE")
        if not is_spring_block(eltype):
            continue
        rows = []
        for line in block.data_lines:
            ids = [str_to_int(t) for t in line.split(",") if t.strip()]
            rows.append((ids[0], [fem.nodes.from_id(n) for n in ids[1:]]))
        blocks.append((block, rows))
        rows_per_elset[(block.params.get("ELSET") or "").lower()] += len(rows)

    for block, rows in blocks:
        eltype = block.params.get("TYPE").upper()
        elset = block.params.get("ELSET")
        named = comment_property(block, "Spring", "Nset")
        if eltype in SPRING_TYPES:
            behaviour = behaviours.get((elset or "").lower())
            if behaviour is None:
                _report().omitted(_STAGE, "*ELEMENT", elset or eltype, f"a {eltype} element with no *Spring on its set")
                continue
            dofs, value = behaviour
            nodes_per_el = 1 if eltype == "SPRING1" else 2
        else:
            user = user_elements.get(eltype)
            if user is None or user[0] != 1 or len(user[1]) > 6:
                _report().omitted(
                    _STAGE, "*ELEMENT", elset or eltype, f"user element {eltype} is not a one-node linear stiffness"
                )
                continue
            nodes_per_el = 1
        for el_id, nodes in rows:
            if len(nodes) != nodes_per_el:
                raise ValueError(f"abaqus read: {eltype} element {el_id} has {len(nodes)} nodes")
            if "Spring" in named:
                name = named["Spring"]
            elif rows_per_elset[(elset or "").lower()] == 1 and elset:
                name = elset
            else:
                name = f"{elset or eltype.lower()}_{el_id}"
            spring = springs.get(name)
            if spring is None:
                spring = springs[name] = [el_id, nodes, eltype, np.zeros((6, 6)), named.get("Nset", f"{name}_set")]
            k = spring[3]
            if eltype == "SPRING1":
                k[dofs[0] - 1, dofs[0] - 1] += value
            elif eltype == "SPRING2":
                k[dofs[0] - 1, dofs[1] - 1] += value
            else:
                _, active, matrix = user
                idx = np.array(active) - 1
                k[np.ix_(idx, idx)] += matrix
                spring[2] = "SPRING1"

    # The node sets are registered by link_spring_sets, once the deck's own sets are read.
    return [
        Spring(name, el_id, eltype, stiff=k, fem_set=FemSet(nset, nodes, FemSet.TYPES.NSET, parent=fem), parent=fem)
        for name, (el_id, nodes, eltype, k, nset) in springs.items()
    ]


def link_spring_sets(fem: FEM) -> None:
    """Point each spring at the deck's node set of its name, or register the one it was built
    with when the deck has none."""
    for spring in fem.springs.values():
        existing = fem.sets.nodes.get(spring.fem_set.name)
        if existing is None:
            fem.sets.add(spring.fem_set)
        else:
            spring._fem_set = existing
