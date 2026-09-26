"""Springs, as the Sesam interface file defines them (section 5, table 5.1; section 7.4).

* A grounded spring (``SPRING1``) is element type 18, GSPR, with an MGSPRNG record: the full
  NDOF x NDOF stiffness, coupled terms and all.
* A two-node spring (``SPRING2``) is element type 40, GLSH, with an MSHGLSP record of kind 2
  (general spring): a (NDOF1 + NDOF2)-square stiffness. adapy's ``SPRING2`` stiffness holds
  Abaqus's meaning -- the term ``k`` at ``(i, j)`` is a spring between DOF ``i`` of the first
  node and DOF ``j`` of the second -- so the 12 x 12 matrix is assembled from those links, and
  the reader takes it apart again.

Both records store the lower triangle column by column (``K(1,1), K(2,1) ... K(NDOF,1),
K(2,2) ...``). The element refers to its record by GELREF1's MATNO, which shares its numbering
with the materials, so springs take the numbers after the last material's.

Sesam has no name for a spring and no link to its node set, so both go on a TDELEM record
(section 4.2.1): the spring's name as the element name, and ``Nset: <set>`` as its comment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.fem.shapes.definitions import SpringTypes

from .write_utils import write_ff

if TYPE_CHECKING:
    from ada.fem import Spring

GROUND_SPRING = 18
TWO_NODE_SPRING = 40
GENERAL_SPRING = 2  # MSHGLSP's MATKND for a spring (1 is a shim)
NSET_COMMENT = "Nset: "


def spring_matnos(springs: Iterable[Spring], first: int) -> dict[int, int]:
    """The MATNO of each spring's stiffness record, keyed by element id."""
    return {sp.id: first + i for i, sp in enumerate(sorted(springs, key=lambda s: s.id))}


def two_node_matrix(stiff) -> np.ndarray:
    """The 12 x 12 stiffness of adapy's SPRING2 links: ``k`` at ``(i, j)`` resists
    ``u1_i - u2_j``."""
    k6 = np.asarray(stiff, dtype=float)
    n = k6.shape[0]
    k = np.zeros((2 * n, 2 * n))
    for i, j in zip(*np.nonzero(k6)):
        v = k6[i, j]
        k[i, i] += v
        k[n + j, n + j] += v
        k[i, n + j] -= v
        k[n + j, i] -= v
    return k


def _lower_by_columns(k: np.ndarray) -> list[float]:
    n = k.shape[0]
    return [float(k[i, j]) for j in range(n) for i in range(j, n)]


def _rows(values: list, head: tuple) -> list[tuple]:
    """A record's fields four to a line, after the ``head`` fields that open it."""
    flat = list(head) + values
    return [tuple(flat[i : i + 4]) for i in range(0, len(flat), 4)]


def element_records(spring: Spring, matno: int) -> tuple[str, str]:
    """The spring's GELMNT1 and GELREF1."""
    eltyp = GROUND_SPRING if spring.type == SpringTypes.SPRING1 else TWO_NODE_SPRING
    nodes = tuple(n.id for n in spring.nodes)
    gelmnt = write_ff("GELMNT1", [(spring.id, spring.id, eltyp, 0), nodes])
    gelref = write_ff("GELREF1", [(spring.id, matno, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)])
    return gelmnt, gelref


def property_records(spring: Spring, matno: int) -> str:
    """The stiffness record and the TDELEM that names the spring."""
    k6 = np.asarray(spring.stiff, dtype=float)
    if spring.type == SpringTypes.SPRING1:
        n = k6.shape[0]
        out = write_ff("MGSPRNG", _rows(_lower_by_columns(k6), (matno, n)))
    else:
        n = k6.shape[0]
        k = two_node_matrix(k6)
        out = write_ff("MSHGLSP", _rows(_lower_by_columns(k), (matno, GENERAL_SPRING, n, n)))
    name, comment = spring.name, f"{NSET_COMMENT}{spring.fem_set.name}"
    out += write_ff("TDELEM", [(4, spring.id, 100 + len(name), 100 + len(comment)), (name,), (comment,)])
    return out
