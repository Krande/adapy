"""Springs, written the way :mod:`..read.read_springs` reads them back.

A diagonal stiffness goes out as ``SPRING1`` (``SPRING2`` for a two-node spring) elements, one
per non-zero term: each element in its own set, with a ``*Spring`` giving the DOF and the
value. The first element keeps the spring's id; the others take ids past the part's highest.
A grounded spring whose DOFs are coupled goes out as a linear user element holding the whole
matrix. Every element is written under a ``** Spring: <name>  Nset: <set>`` comment, which is
how the reader puts the spring back together.

The writer this replaced wrote each term as ``*Spring, elset=<the node set>`` followed by the
element's id and node as a data line, with no ``*Element`` anywhere. Abaqus read those as
stiffness tables of a set that holds no elements. It also wrote values to 7 significant
digits and dropped every off-diagonal term without saying so.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ..grammar import format_number, render_keyword

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Spring

_STAGE = "abaqus writer"


def springs_str(fem: FEM) -> str:
    springs = list(fem.springs.values())
    if not springs:
        return "** No Springs"
    fresh_ids = itertools.count(fem.elements.max_el_id + 1)
    user_types = itertools.count(_user_types_before(fem) + 1)
    return "".join(spring_str(s, fresh_ids, user_types) for s in springs).rstrip()


def _is_coupled(spring: Spring) -> bool:
    k = np.asarray(spring.stiff, dtype=float)
    return spring.type.value == "SPRING1" and np.count_nonzero(k - np.diag(np.diag(k))) > 0


def _user_types_before(fem: FEM) -> int:
    """Linear user element types are named ``U<n>`` model-wide: count the ones parts written
    earlier have already taken."""
    parent = fem.parent
    if parent is None:
        return 0
    n = 0
    for part in parent.get_assembly().get_all_parts_in_assembly(include_self=True):
        if part.fem is fem:
            break
        n += sum(_is_coupled(s) for s in part.fem.springs.values())
    return n


def _terms(spring: Spring) -> Iterator[tuple[tuple[int, ...], float]]:
    k = np.asarray(spring.stiff, dtype=float)
    two_node = spring.type.value == "SPRING2"
    for i, j in zip(*np.nonzero(k)):
        if two_node:
            yield (i + 1, j + 1), k[i, j]
        elif i == j:
            yield (i + 1,), k[i, j]


def spring_str(spring: Spring, fresh_ids: Iterator[int], user_types: Iterator[int]) -> str:
    from ada.fem.formats import conversion_report
    from ada.fem.shapes.definitions import SpringTypes

    if spring.type not in (SpringTypes.SPRING1, SpringTypes.SPRING2):
        raise ValueError(f'Currently unsupported spring type "{spring.type}"')

    comment = [f"Spring: {spring.name}  Nset: {spring.fem_set.name}"]
    nodes = ", ".join(str(n.id) for n in spring.nodes)
    k = np.asarray(spring.stiff, dtype=float)

    if _is_coupled(spring):
        if not np.allclose(k, k.T, rtol=0.0, atol=0.0):
            conversion_report.current().omitted(
                _STAGE, "*SPRING", spring.name, "an unsymmetric spring stiffness has no Abaqus form here"
            )
            return ""
        utype = f"U{next(user_types)}"
        n = k.shape[0]
        lower = [format_number(v) for v in k[np.tril_indices(n)]]
        return (
            render_keyword("User Element", [("type", utype), ("nodes", 1), ("linear", None)], [range_str(n)])
            + render_keyword(
                "Matrix", [("type", "STIFFNESS")], [", ".join(lower[i : i + 4]) for i in range(0, len(lower), 4)]
            )
            + render_keyword("Element", [("type", utype), ("elset", spring.name)], [f"{spring.id}, {nodes}"], comment)
        )

    out = ""
    terms = list(_terms(spring)) or [((1, 1) if spring.type == SpringTypes.SPRING2 else (1,), 0.0)]
    for n, (dofs, value) in enumerate(terms):
        el_id = spring.id if n == 0 else next(fresh_ids)
        elset = spring.name if len(terms) == 1 else f"{spring.name}_{'_'.join(map(str, dofs))}"
        out += render_keyword(
            "Element", [("type", spring.type.value), ("elset", elset)], [f"{el_id}, {nodes}"], comment
        )
        out += render_keyword("Spring", [("elset", elset)], [", ".join(map(str, dofs)), format_number(value)])
    return out


def range_str(n: int) -> str:
    return ", ".join(str(i) for i in range(1, n + 1))
