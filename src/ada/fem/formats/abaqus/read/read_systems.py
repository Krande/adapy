"""``*System`` -- the local coordinate system the node coordinates that follow it are given in.

This keyword used to be skipped, and skipping it does not lose a construct: it *moves the mesh*.
A deck holding

.. code-block:: none

    *System
              0.,       -660.,          0.,          1.,       -660.,          0.
    *Node
     689511,           0.,           0.,           0.

read node 689511 at the origin instead of at ``(0, -660, 0)`` -- jacket-leg beam nodes 660 mm away
from where the deck puts them, in a model that otherwise looked complete. The keyword census
(``lexer.track_reads`` / ``reader.report_unread_keywords``) does report ``*SYSTEM`` as unread, so
the omission is at least visible; what it cannot say is that the nodes are in the wrong place. So
the transform is applied here, and the cases this module cannot determine are reported rather than
guessed at.

**What the keyword means.** The data are, in order, point *a* (the new origin), point *b* (a point
on the local X axis) and point *c* (a point in the local X-Y plane); *b* and *c* are optional, so a
block carries 3, 6 or 9 numbers. A ``*System`` with no data line resets to the global frame, and it
is the reset that makes the keyword safe to read in document order: the system in force for a
``*Node`` block is the last ``*System`` before it.

**What is applied, and what is reported instead.**

===================================== ==============================================================
3 numbers (*a* only)                  a translation; applied
6 numbers, *a* to *b* along global X  a translation; applied (the rotation is the identity)
6 numbers, *a* to *b* anywhere else   **reported as omitted**; coordinates untouched
9 numbers (*a*, *b*, *c*)             translation and rotation; applied
===================================== ==============================================================

The 6-number off-axis case is the one honest gap. Without *c* the local Y and Z axes are not fixed
by the three numbers the deck gives, and this reader will not invent a completion rule and move
real nodes by it -- a wrong coordinate is worse than a reported one. It is an ``omitted`` finding,
not a ``suspect`` one: the deck is not suspect, *this reader* did not carry the construct across.

**Scope.** The system in force is resolved within the string being parsed, which is the string the
``*Node`` blocks are parsed from -- the whole deck for a flat deck, the part body for a
``*Part``/``*End Part`` deck. A ``*System`` outside that string governs no node block inside it.
"""

from __future__ import annotations

import dataclasses
import re

import numpy as np

from ada.fem.formats import conversion_report

from .lexer import iter_keywords

#: The stage every finding from here carries. Same reader, same group in the report.
STAGE = "abaqus reader"

#: Cheap pre-check: skipping the keyword scan entirely on a deck with no ``*System`` keeps an
#: 80 MB read from paying for a construct it does not contain.
_HAS_SYSTEM = re.compile(r"^[ \t]*\*[ \t]*system\b", re.IGNORECASE | re.MULTILINE)

#: Direction cosines closer than this to a global axis are that axis. Deck coordinates are written
#: with ~9 significant digits, so this is loose enough for a written-out unit vector and far tighter
#: than any real rotation.
_TOL = 1e-9

#: How many ``*System`` line numbers a finding carries: enough to go and look, not a flood.
MAX_REPORTED_LINES = 3


@dataclasses.dataclass(frozen=True)
class LocalSystem:
    """One ``*System`` block: where it is in the deck and what it does to a coordinate."""

    #: Offset of the ``*System`` keyword line in the string it was found in.
    start: int
    #: 1-based line number of the keyword line, for a human who has to go and look.
    line: int
    #: Point *a*: the local origin, in global coordinates.
    origin: np.ndarray
    #: Local axes as the columns of a rotation matrix, or ``None`` when they are the global ones.
    rotation: np.ndarray | None
    #: ``True`` when the block gives *a* and *b* but no *c* and *b* is off the global X axis, so
    #: the local Y and Z axes are not determined. Nothing is applied for such a block.
    undetermined: bool = False

    @property
    def is_global(self) -> bool:
        """Does this block leave coordinates exactly as the deck writes them?"""
        return not self.undetermined and self.rotation is None and not self.origin.any()

    def to_global(self, xyz: np.ndarray) -> np.ndarray:
        """``(n, 3)`` coordinates in this system, as global coordinates."""
        if self.rotation is not None:
            xyz = xyz @ self.rotation.T
        return xyz + self.origin


def find_local_systems(bulk_str: str) -> list[LocalSystem]:
    """Every ``*System`` block in ``bulk_str``, in document order.

    Raises ``ValueError`` on a block whose data are not 3, 6 or 9 numbers, or whose points are
    degenerate. That is a malformed deck rather than an unsupported construct: every node after it
    would be at a coordinate nobody can work out, and there is no correct subset to write.
    """
    if _HAS_SYSTEM.search(bulk_str) is None:
        return []

    systems: list[LocalSystem] = []
    for block in iter_keywords(bulk_str, "SYSTEM"):
        values = [float(f) for line in block.data_lines for f in line.split(",") if f.strip()]
        systems.append(_system_from_values(values, block.start, block.lineno))
    return systems


def system_in_force(systems: list[LocalSystem], pos: int) -> LocalSystem | None:
    """The last ``*System`` before ``pos``, or ``None`` when no ``*System`` precedes it."""
    in_force = None
    for system in systems:
        if system.start >= pos:
            break
        in_force = system
    return in_force


def _system_from_values(values: list[float], start: int, line: int) -> LocalSystem:
    zero = np.zeros(3)
    if len(values) == 0:
        # An empty *System resets to the global frame. This is why reading the keyword in document
        # order is enough: a deck that uses local systems also closes them.
        return LocalSystem(start, line, zero, None)
    if len(values) not in (3, 6, 9):
        raise ValueError(
            f"*System on line {line} carries {len(values)} numbers; Abaqus allows 3 (origin), "
            "6 (origin and a point on the local X axis) or 9 (and a point in the local X-Y plane)"
        )

    a = np.array(values[:3], dtype=np.float64)
    if len(values) == 3:
        return LocalSystem(start, line, a, None)

    b = np.array(values[3:6], dtype=np.float64)
    e1 = b - a
    length = float(np.linalg.norm(e1))
    if length == 0.0:
        raise ValueError(f"*System on line {line} puts point b on top of the origin, so the local X axis is undefined")
    e1 = e1 / length

    if len(values) == 6:
        if abs(e1[0] - 1.0) < _TOL and abs(e1[1]) < _TOL and abs(e1[2]) < _TOL:
            # Local X is the global X, so the local axes are the global axes and only the origin
            # moves. This is what Abaqus/CAE writes for a translated part instance.
            return LocalSystem(start, line, a, None)
        return LocalSystem(start, line, a, None, undetermined=True)

    c = np.array(values[6:9], dtype=np.float64)
    in_plane = c - a
    e2 = in_plane - float(in_plane @ e1) * e1
    width = float(np.linalg.norm(e2))
    if width == 0.0:
        raise ValueError(f"*System on line {line} puts point c on the local X axis, so the X-Y plane is undefined")
    e2 = e2 / width
    rotation = np.column_stack([e1, e2, np.cross(e1, e2)])
    if np.allclose(rotation, np.eye(3), atol=_TOL):
        # Keep the identity out of the arithmetic: a deck that spells the global frame out must
        # read back byte-for-byte what it wrote.
        return LocalSystem(start, line, a, None)
    return LocalSystem(start, line, a, rotation)


class SystemTally:
    """Applies the systems over one pass of ``*Node`` blocks and reports the pass once.

    One finding per pass, not one per block: the numbers an engineer needs are how many nodes moved
    and how many did not, and a finding per ``*System`` would put the first block's node count on
    all of them.
    """

    def __init__(self):
        self.moved_nodes = 0
        self.moved_systems = 0
        self.moved_lines: list[int] = []
        self.unapplied_nodes = 0
        self.unapplied_systems = 0
        self.unapplied_lines: list[int] = []

    def to_global(self, xyz: np.ndarray, system: LocalSystem | None) -> np.ndarray:
        """``(n, 3)`` coordinates read under ``system``, as global coordinates."""
        if system is None or system.is_global or len(xyz) == 0:
            return xyz
        if system.undetermined:
            self.unapplied_nodes += len(xyz)
            self.unapplied_systems += 1
            if len(self.unapplied_lines) < MAX_REPORTED_LINES:
                self.unapplied_lines.append(system.line)
            return xyz
        self.moved_nodes += len(xyz)
        self.moved_systems += 1
        if len(self.moved_lines) < MAX_REPORTED_LINES:
            self.moved_lines.append(system.line)
        return system.to_global(xyz)

    def report(self) -> None:
        """Say what the local systems did, and what could not be done."""
        report = conversion_report.current()
        if self.unapplied_nodes:
            report.omitted(
                STAGE,
                "*SYSTEM",
                "",
                "the local coordinate system gives no point in its X-Y plane, so its rotation is "
                "not determined and the node coordinates that follow it are read as global",
                count=self.unapplied_systems,
                nodes=self.unapplied_nodes,
                lines=self.unapplied_lines,
            )
        if self.moved_nodes:
            report.note(
                STAGE,
                "*SYSTEM",
                "",
                "node coordinates were transformed from a local *System into the global frame",
                count=self.moved_systems,
                nodes=self.moved_nodes,
                lines=self.moved_lines,
            )
