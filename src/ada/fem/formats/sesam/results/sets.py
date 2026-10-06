"""Sesam named sets, as the viewer manifest carries them.

A Sesam result deck names its sets in ``TDSETNAM`` and lists their members in
``GSETMEMB``. Both readers already parse those records — :class:`SifReader` for
SIF text and :class:`SinReader` for the SIN binary, which inherits the same
decoders — but nothing turned them into the ``groups`` block the bake writes into
``fea.manifest.json``. Both stream readers' ``try_groups()`` returned ``None``,
so a result deck reached the viewer with no sets at all, and the FEM-bake path's
comment ("readers without the method (SIF/SIN/RMED) contribute nothing") was a
statement of the gap rather than of a design.

That is what this closes. It matters beyond the Groups picker: a set is how you
say WHICH PART of a model an operation applies to — scoping a capacity check to
one plate group, isolating a deck — and without the names reaching the browser, every
such control had to be a text box you typed a name into from memory.

Member ids are the numbers people read, prefixed the way the frontend's
draw-range lookup expects (``EL`` / ``P``). ``GSETMEMB`` lists INTERNAL node and
element numbers (the manual: "program defined internal node/element numbers"),
which is what the mesh is keyed by; the bake labels the mesh with the EXTERNAL
numbers (GNODE NODEX, GELMNT1 ELNOX), which GeniE and Xtract show, so the
members are translated to those. On a deck whose two numberings agree that is
no change.
"""

from __future__ import annotations

from typing import Any

__all__ = ["manifest_groups"]


def manifest_groups(reader: Any) -> list[dict[str, Any]] | None:
    """Named sets from a Sesam reader, shaped for ``manifest["groups"]``.

    ``None`` when the deck names no sets — the manifest omits the key entirely
    rather than carrying an empty list, so the frontend's "does this result have
    groups at all" test stays a simple presence check.

    A set may carry BOTH node (``ISTYPE`` 1) and element (``ISTYPE`` 2) records.
    Those become two entries rather than one, because they are two different
    things to the viewer: an element set can be isolated in the 3D view, a node
    set names vertices that carry no triangles and can only be listed. Collapsing
    them would make one of the two silently unusable.
    """
    try:
        members_by_set = reader.get_gsetmemb()
        names = reader.get_tdsetnam_map()
    except (AttributeError, NotImplementedError):
        return None
    if not members_by_set or not names:
        return None
    try:
        node_shown, elem_shown = reader.external_number_maps()
    except (AttributeError, NotImplementedError):
        node_shown, elem_shown = None, None

    groups: list[dict[str, Any]] = []
    for set_id, by_type in members_by_set.items():
        record = names.get(set_id)
        if record is None:
            # A set with members and no name. Nothing sensible to call it, and a
            # made-up label in a picker is worse than an absence.
            continue
        name = str(record[-1]).strip()
        if not name:
            continue
        for kind, prefix, ids, shown in (
            ("element", "EL", by_type.get("elset") or [], elem_shown or {}),
            ("node", "P", by_type.get("nset") or [], node_shown or {}),
        ):
            if not ids:
                continue
            groups.append(
                {
                    "name": name,
                    "members": [f"{prefix}{shown.get(int(i), int(i))}" for i in ids],
                    "fe_object_type": kind,
                }
            )
    return groups or None
