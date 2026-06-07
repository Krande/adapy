"""Concatenate the FEMs of a multi-part/instance assembly into one part's FEM.

Single-part FEM writers (Sesam ``.FEM``, Code_Aster ``.med``, Genie ``.xml``) can only emit
one part. A FEM imported from a multi-instance deck (e.g. Abaqus assembly with several part
instances) carries one ``Part.fem`` per instance, with independently-numbered nodes/elements
and possibly same-named sets across instances. This folds them into a single ``Part.fem``:

* node/element ids are renumbered to avoid cross-instance conflicts (handled by ``FEM.__add__``);
* set names are prefixed with the source instance name so same-named sets stay distinct
  (``FemSets.__add__`` merges sets that share a name);
* the folded-from parts are emptied so single-part writers then see exactly one FEM part.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part
    from ada.fem import FEM


def _prefix_set_names(fem: "FEM", prefix: str) -> None:
    """Prefix every set name in ``fem`` with ``prefix`` and rebuild the set container so the
    name→set maps reflect the new names."""
    from ada.fem.containers import FemSets

    sets = list(fem.sets)
    if not sets:
        return
    for s in sets:
        if not s.name.startswith(f"{prefix}_"):
            s.name = f"{prefix}_{s.name}"
    fem.sets = FemSets(sets, parent=fem)


def concatenate_fem_to_single_part(assembly: "Assembly") -> "Part | None":
    """Fold all FEM-bearing parts of ``assembly`` into the first one's FEM, in place.

    No-op (returns the single part, or None) when there is nothing to merge. Returns the part
    that now holds the combined FEM."""
    from ada.fem import FEM

    parts = [p for p in assembly.get_all_subparts(include_self=True) if p.fem is not None and len(p.fem.nodes) > 0]
    if len(parts) <= 1:
        return parts[0] if parts else None

    # Disambiguate set names with the source instance name when those are all distinct
    # (Abaqus multi-instance decks); otherwise fall back to the always-unique part name.
    inames = [p.fem.instance_name for p in parts]
    use_instance = all(inames) and len(set(inames)) == len(inames)
    prefix_of = {id(p): (p.fem.instance_name if use_instance else p.name) for p in parts}

    base = parts[0]
    _prefix_set_names(base.fem, prefix_of[id(base)])
    for p in parts[1:]:
        _prefix_set_names(p.fem, prefix_of[id(p)])
        base.fem += p.fem
        # Empty the folded-from part so single-part writers see only `base`.
        p.fem = FEM(name=f"{p.name}_merged_away", parent=p)

    logger.info(f"Concatenated {len(parts)} FEM parts into '{base.name}' ({len(base.fem.nodes)} nodes)")
    return base
