"""One name de-duplicator for everything a DEXPI import and export keys by name.

Equipment names, catalog slugs, port names and the merge writer's reconstruction of all three
share a rule -- the first claimant keeps the bare name and the rest get ``-2``, ``-3`` -- and that
rule used to be written out five times, in three modules. It has to be *one* rule: the writer
recomputes the importer's names from the source document to find its way back to each item (see
:mod:`ada.cadit.dexpi.write.from_ada`), so two copies that drift apart by a single edge case mint
a duplicate on an unedited round-trip.

The five copies did not agree on what happens to an **empty** candidate, and that difference is
kept rather than papered over: :func:`unique_name` takes the ``fallback`` word each caller used
and a ``style`` naming which stem the number hangs off. Every former output is pinned in
``tests/core/cadit/dexpi/test_naming.py``.
"""

from __future__ import annotations

from typing import Literal

__all__ = ["SuffixStyle", "unique_name"]

#: Which stem the ``-2``, ``-3`` numbering hangs off when the candidate had to fall back.
#:
#: ``"on-candidate"`` numbers the candidate as given, so an empty candidate that fell back to
#: ``"equipment"`` is followed by ``"-2"`` -- the equipment and slug pools have always done this
#: and the writer's identity recomputation depends on it staying so. ``"on-name"`` numbers the
#: resolved name, so ``"port"`` is followed by ``"port-2"``, which is what port naming does.
#: The two only differ for an empty candidate.
SuffixStyle = Literal["on-candidate", "on-name"]


def unique_name(
    candidate: str,
    taken: set[str],
    *,
    fallback: str = "",
    style: SuffixStyle = "on-candidate",
) -> str:
    """``candidate``, suffixed ``-2``, ``-3``, ... until it is not in ``taken``, then claimed there.

    ``fallback`` answers for an empty candidate (``""`` keeps it empty). ``style`` says which stem
    the number hangs off -- see :data:`SuffixStyle`. The pool is shared by reference: pass the same
    set for everything that lands in one name-keyed map, so a tag a P&ID happens to reuse cannot
    let one item shadow another.
    """
    name = candidate or fallback
    stem = name if style == "on-name" else candidate
    suffix = 1
    while name in taken:
        suffix += 1
        name = f"{stem}-{suffix}"
    taken.add(name)
    return name
