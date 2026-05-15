"""FemSet name shortening for Code Aster's 24-character GROUP_MA / GROUP_NO limit.

Code Aster's MED reader (bibcxx/IOManager/MedToAsterReader.cxx, lines
~492 + ~510) silently drops any node-group or cell-group name longer
than 24 characters with an MED_7 alarm. Adapy FemSets routinely produce
names well above that (35-50 chars for stiffened-plate structures), so
every GROUP_MA / GROUP_NO reference in the resulting .comm file lands
in an empty mesh and AFFE_MODELE fails with MODELISA7_12.

This module mints deterministic ≤24-char ids for every FemSet, mutates
the FemSet objects in-place to use the short ids (mirroring
:func:`ada.materials.utils.shorten_material_names`), and writes a
``<name>.name_map.json`` sidecar next to the analysis output so the
short ids can be mapped back to their original long names when
interpreting .rmed results.

Round-trip semantics: a re-run on an already-shortened assembly is a
no-op (names ≤ 24 chars are left alone), so the JSON sidecar is the
authoritative long-name source.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import FEM
    from ada.api.spatial import Assembly

# Code Aster MedToAsterReader enforces this limit; keep a couple of
# chars headroom for safety (eg. the BCs / loads / sections code paths
# that build names like f"{set}_bc" later in the .comm pipeline).
MAX_GROUP_NAME = 24


def _short_id(original: str) -> str:
    """Stable ≤MAX_GROUP_NAME id derived from the SHA1 of the original."""
    digest = hashlib.sha1(original.encode("utf-8")).hexdigest()
    return f"g_{digest[:12]}"  # 14 chars, well under the 24-char ceiling


def _iter_fems(assembly: Assembly):
    """Yield every FEM container under ``assembly`` (assembly + parts)."""
    yield assembly.fem
    for part in assembly.get_all_parts_in_assembly(True):
        if part.fem is assembly.fem:
            continue
        yield part.fem


def build_name_map(assembly: Assembly) -> dict[str, str]:
    """Collect every FemSet name in ``assembly`` and assign a short id.

    Returns a dict mapping the (possibly long) original name to its short
    id. Names already within ``MAX_GROUP_NAME`` are returned unchanged so
    we don't churn legacy short names through the hasher.
    """
    mapping: dict[str, str] = {}
    used: set[str] = set()
    salt = 0

    def assign(name: str) -> None:
        if name in mapping:
            return
        if len(name) <= MAX_GROUP_NAME:
            mapping[name] = name
            used.add(name)
            return
        candidate = _short_id(name)
        # Vanishingly unlikely but detect SHA1 truncation collisions
        # deterministically by appending a salt-derived suffix.
        nonlocal salt
        while candidate in used:
            salt += 1
            candidate = _short_id(f"{name}#{salt}")
        mapping[name] = candidate
        used.add(candidate)

    for fem in _iter_fems(assembly):
        for elset in fem.elsets.values():
            assign(elset.name)
        for nset in fem.nsets.values():
            assign(nset.name)
        for surface in getattr(fem, "surfaces", {}).values():
            assign(surface.name)
        for bc in fem.bcs:
            if bc.fem_set is not None:
                assign(bc.fem_set.name)
        for step in fem.steps:
            for bc in step.bcs.values():
                if bc.fem_set is not None:
                    assign(bc.fem_set.name)
            for load in step.loads:
                fem_set = getattr(load, "fem_set", None)
                if fem_set is not None:
                    assign(fem_set.name)
                surface = getattr(load, "surface", None)
                if surface is not None:
                    assign(surface.name)

    return mapping


def _rename_fem_set(fem_set, new_name: str, fem: FEM) -> None:
    """Mutate the FemSet's name AND its key in fem.elsets/fem.nsets."""
    old_name = fem_set.name
    if old_name == new_name:
        return
    fem_set.name = new_name
    for container in (fem.elsets, fem.nsets):
        if container is None:
            continue
        if old_name in container and container[old_name] is fem_set:
            container.pop(old_name)
            container[new_name] = fem_set


def apply_name_map(assembly: Assembly, mapping: dict[str, str]) -> None:
    """Mutate every FemSet in ``assembly`` so its name is the mapped short id.

    Walks the same FemSet locations as :func:`build_name_map` so the two
    stay in sync. Also fixes up the elsets/nsets dict keys so the
    container-level lookups continue to work post-rename.
    """
    for fem in _iter_fems(assembly):
        for fem_set in list(fem.elsets.values()):
            new_name = mapping.get(fem_set.name, fem_set.name)
            _rename_fem_set(fem_set, new_name, fem)
        for fem_set in list(fem.nsets.values()):
            new_name = mapping.get(fem_set.name, fem_set.name)
            _rename_fem_set(fem_set, new_name, fem)
        for surface in getattr(fem, "surfaces", {}).values():
            if surface.name in mapping:
                surface.name = mapping[surface.name]


def dump_name_map(mapping: dict[str, str], path: pathlib.Path) -> None:
    """Write the short_id → original_name reverse map as JSON.

    The sidecar lives next to the analysis deck so result post-processing
    can recover original FemSet names from the .rmed / .mess outputs.
    Only entries where the name was actually shortened are emitted —
    short pass-through names don't need recording.
    """
    reverse = {short: original for original, short in mapping.items() if short != original}
    pathlib.Path(path).write_text(json.dumps(reverse, indent=2, sort_keys=True))


__all__ = [
    "MAX_GROUP_NAME",
    "build_name_map",
    "apply_name_map",
    "dump_name_map",
]
