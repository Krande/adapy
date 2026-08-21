"""Named sets and model structure from a Sesam SIN.

The streaming bake already had a slot for this: the reader protocol declares
``try_groups()``, the bake calls it, and the manifest carries the result straight through to
the viewer's group picker. The SIN reader answered ``None``, so a Sesam result arrived in the
browser with a mesh and no vocabulary — no set names, no way to isolate a region, nothing to
say what the model was assembled from. Everything downstream was already built and waiting.

Kept out of ``read_sin.py`` because that module is about decoding result records; this is
about the model's *description*, and the two get read at different times for different
reasons.
"""

from __future__ import annotations

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.sin_reader import SinFile

__all__ = ["read_sin_groups", "read_sin_model_info"]


def read_sin_groups(sin: SinFile) -> list[dict] | None:
    """Named node/element sets, in the manifest's group shape.

    Sesam stores a set as ``GSETMEMB`` member records keyed by a set id (``isref``), and the
    set's *name* separately in ``TDSETNAM`` under the same id. Neither is much use alone: the
    members have no name and the name has no members.

    Three properties of the format that the obvious implementation gets wrong:

    * **A set spans several records.** ``GSETMEMB`` is emitted in chunks, so members must be
      accumulated per id — taking the first record found gives a truncated set, which is the
      worst kind of wrong because it looks plausible.
    * **One set id can hold BOTH kinds.** Node and element members arrive as separate records
      under a single ``isref`` (``istype`` 1 = nodes, 2 = elements). The manifest's group
      shape is single-kind, so a mixed set becomes two groups — suffixed, because two
      identically named rows with no way to tell them apart is not a choice.
    * **Ids are file ids, not indices.** Members are emitted as ``E{id}`` for elements and
      ``P{id}`` for nodes -- the exact draw-range ids the streaming loader builds from the
      AFEM element table (``E${label}``). The prefix is not cosmetic: members that do not
      match a range id select nothing, silently, while still counting as "897 selected" in
      the UI -- a set that looks like it worked and did nothing. Array indices would fail
      the same quiet way the moment a re-bake reordered elements.

    Returns ``None`` when the file carries no sets — what the bake expects from a reader with
    nothing to contribute.
    """
    from ada.fem.formats.sesam.results.read_sin import _records_for

    member_rows = _records_for(sin, cards.GSETMEMB)
    if not member_rows:
        return None

    isref_i, istype_i = cards.GSETMEMB.get_indices_from_names(["isref", "istype"])
    members_i = cards.GSETMEMB.components.index("members")

    # (set id, istype) -> member ids, accumulated across records.
    by_key: dict[tuple[int, int], list[int]] = {}
    for row in member_rows:
        try:
            set_id = int(row[isref_i])
            istype = int(row[istype_i])
        except (IndexError, TypeError, ValueError):
            continue
        ids = [int(v) for v in row[members_i:] if isinstance(v, (int, float))]
        if ids:
            by_key.setdefault((set_id, istype), []).extend(ids)

    if not by_key:
        return None

    names: dict[int, str] = {}
    name_isref_i = cards.TDSETNAM.components.index("isref")
    for row in _records_for(sin, cards.TDSETNAM):
        try:
            set_id = int(row[name_isref_i])
        except (IndexError, TypeError, ValueError):
            continue
        text = row[-1]
        if isinstance(text, str) and text.strip():
            names[set_id] = text.strip()

    kinds_per_id: dict[int, int] = {}
    for set_id, _istype in by_key:
        kinds_per_id[set_id] = kinds_per_id.get(set_id, 0) + 1

    groups: list[dict] = []
    for (set_id, istype), ids in sorted(by_key.items()):
        is_node = istype == 1
        prefix = "P" if is_node else "E"
        base = names.get(set_id) or f"Set {set_id}"
        # Disambiguate only when the id really carries both kinds. Suffixing every set would
        # put "(elements)" on the majority of names for no reason.
        suffix = "" if kinds_per_id.get(set_id, 1) == 1 else (" (nodes)" if is_node else " (elements)")
        # De-duplicate while keeping order — a member repeated across chunks is one member.
        seen: set[int] = set()
        ordered = [i for i in ids if not (i in seen or seen.add(i))]
        groups.append(
            {
                "name": f"{base}{suffix}",
                "members": [f"{prefix}{i}" for i in ordered],
                "fe_object_type": "node" if is_node else "element",
            }
        )
    return groups or None


def read_sin_model_info(sin: SinFile) -> dict | None:
    """What the model is made of: totals, and the super-element breakdown.

    Answers "how big is this, and what is it assembled from" before any set is chosen —
    the first thing an engineer reads in a result viewer.

    Per-super-element counts are reported only when the file holds exactly ONE
    super-element, where they are the model totals and provably correct. Splitting them
    across several needs the element-to-super-element association, which ``GELMNT1`` does not
    carry here; those report ``null`` counts instead of a fabricated split. A wrong number is
    worse than a missing one in a panel people size work from.
    """
    try:
        n_nodes = int(sin.get_count("GNODE"))
        n_elements = int(sin.get_count("GELMNT1"))
    except Exception:  # noqa: BLE001 — a file without these is not a model we can describe
        return None
    if not (n_nodes or n_elements):
        return None

    try:
        n_super = int(sin.get_count("HIERARCH"))
    except Exception:  # noqa: BLE001
        n_super = 0

    if n_super == 1:
        supers = [{"index": 1, "name": "SE 1", "n_nodes": n_nodes, "n_elements": n_elements}]
    else:
        supers = [{"index": i + 1, "name": f"SE {i + 1}", "n_nodes": None, "n_elements": None} for i in range(n_super)]

    return {"n_nodes": n_nodes, "n_elements": n_elements, "super_elements": supers}
