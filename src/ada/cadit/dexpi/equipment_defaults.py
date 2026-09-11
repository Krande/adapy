"""Physical defaults for a DEXPI equipment class.

A P&ID says a vessel is a ``ProcessColumn``; it never says the column is 2 m across and 15 m tall.
``resources/dexpi_equipment_defaults.json`` supplies that missing half -- an envelope, an IFC class,
a nozzle-layout strategy and an apparent density -- for a few dozen hand-curated classes, and
:func:`resolve_defaults` stretches those over all 142 ``Plant/ProcessEquipment`` classes by walking
up the supertype DAG. A ``CentrifugalPump`` has no entry of its own; it inherits ``Pump``'s.

The table is **ours**, not DEXPI's: curated engineering guesses, deliberately unremarkable, meant to
be overridden per tag or per class by a definition list (:mod:`ada.cadit.dexpi.equipment_list`).
The ``_meta`` block in the JSON says so too, because the generated class table sitting next to it
*is* spec data and the two must not be confused.

Nothing here runs at import time; the JSON loads lazily and is cached.
"""

from __future__ import annotations

import copy
import functools
import json
import pathlib
from typing import Any, Iterable

from ada.core.catalog_docs import validate_equipment_doc

from . import class_table
from .nozzle_placers import NozzleSpec, place_nozzles

__all__ = [
    "build_default_doc",
    "curated_classes",
    "defaults",
    "meta",
    "resolve_defaults",
]

_TABLE_PATH = pathlib.Path(__file__).parent / "resources" / "dexpi_equipment_defaults.json"

#: The catch-all entry name in the table. Every ``Plant/ProcessEquipment`` class reaches it if
#: nothing more specific matches, because it is their common abstract base -- note that this is
#: ``ProcessEquipment`` and not ``Equipment``: DEXPI has no class by the latter name (it is only a
#: Proteus element tag), and ``Chamber``/``Nozzle`` deliberately do not derive from it.
_ROOT_CLASS = "ProcessEquipment"

#: Used only if the JSON is somehow missing its root entry, so a lookup can never return None and
#: leave a caller to invent a size of its own.
_FALLBACK: dict = {
    "bbox": [2.0, 2.0, 2.0],
    "ifc": "IfcBuildingElementProxy",
    "nozzles": "generic",
    "density": 200.0,
}


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    with _TABLE_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def meta() -> dict:
    """Provenance of the curated table -- what it is, what its numbers mean, and whose data it is
    (adapy's, not DEXPI's)."""
    return dict(_load().get("_meta", {}))


def defaults() -> dict:
    """The whole curated table, ``{class name: entry}``. A copy; callers may keep it."""
    return copy.deepcopy(_load().get("defaults", {}))


def curated_classes() -> list[str]:
    """The DEXPI classes that carry an entry of their own, sorted."""
    return sorted(_load().get("defaults", {}))


def resolve_defaults(class_name: str) -> dict:
    """Physical defaults for ``class_name``, resolved up the supertype chain.

    Returns a fresh dict with the entry's ``bbox``/``ifc_element_class``/``nozzles``/``mass``
    resolved plus two provenance keys: ``class`` (which curated entry answered) and ``source``:

    * ``"class"`` -- ``class_name`` has an entry of its own;
    * ``"supertype"`` -- it inherited one, from the *nearest* supertype that has one;
    * ``"fallback"`` -- nothing in its ancestry is curated, so the built-in placeholder answered.
      An unknown vendor class lands here, and so does anything outside the equipment hierarchy.

    DEXPI uses multiple inheritance freely, so "nearest" means fewest hops: the search is
    breadth-first over the supertype DAG, and ties within one level are broken alphabetically so
    the answer never depends on the order the spec happened to list the supertypes in.
    """
    table = _load().get("defaults", {})
    name = class_table.resolve(class_name)

    entry = table.get(name)
    if entry is not None:
        return _entry(name, "class", entry)

    matched = _nearest(name, table)
    if matched is not None:
        return _entry(matched, "supertype", table[matched])

    # Nothing in the ancestry is curated -- a vendor class the spec never declared, or an item from
    # outside the equipment hierarchy. It still gets the catch-all's numbers, but says so.
    return _entry(None, "fallback", table.get(_ROOT_CLASS) or _FALLBACK)


def _nearest(name: str, table: dict) -> str | None:
    """The curated class fewest supertype hops above ``name``, or None."""
    seen = {name}
    level = sorted(class_table.supertypes(name))
    while level:
        for candidate in level:
            if candidate in table:
                return candidate
        next_level: list[str] = []
        for candidate in level:
            if candidate in seen:
                continue
            seen.add(candidate)
            next_level.extend(class_table.supertypes(candidate))
        level = sorted(set(next_level) - seen)
    return None


def _entry(matched: str | None, source: str, entry: dict) -> dict:
    bbox = [float(v) for v in entry.get("bbox", _FALLBACK["bbox"])]
    return {
        "class": matched,
        "source": source,
        "bbox": bbox,
        "ifc_element_class": entry.get("ifc") or _FALLBACK["ifc"],
        "nozzles": entry.get("nozzles") or _FALLBACK["nozzles"],
        "density": entry.get("density"),
        "mass": _mass(entry, bbox),
        "note": entry.get("note"),
    }


def _mass(entry: dict, bbox: list[float]) -> float:
    """Mass in kg: the entry's explicit ``mass`` if it has one, else its apparent envelope density
    times the volume of ``bbox``. Deriving it from the box is what lets an overridden size carry a
    proportionate mass instead of the class default's."""
    explicit = entry.get("mass")
    if explicit is not None:
        return float(explicit)
    density = float(entry.get("density") or _FALLBACK["density"])
    return round(density * bbox[0] * bbox[1] * bbox[2], 3)


def build_default_doc(
    class_name: str,
    nozzles: Iterable[NozzleSpec] = (),
    *,
    bbox: Any = None,
    strategy: str | None = None,
    mass: float | None = None,
    cog: Iterable[float] | None = None,
    ifc_element_class: str | None = None,
    tag: str | None = None,
    dexpi_id: str | None = None,
) -> dict:
    """The catalog equipment document for a DEXPI item of class ``class_name``.

    ``nozzles`` are the item's actual connections (:class:`~.nozzle_placers.NozzleSpec`); they are
    laid out on the resolved envelope by the class's strategy, so two items of the same class with
    different nozzle counts get different -- and individually sensible -- port lists.

    ``bbox``/``strategy``/``mass``/``ifc_element_class`` override the class default before the
    ports are generated, which matters: ports placed against the default envelope and then handed
    an overridden one would sit off the box.

    DEXPI provenance (``dexpi_class``, ``dexpi_id``, ``tag``) and the layout strategy ride on the
    document as extra keys. ``EquipmentTypeDoc`` is ``extra="allow"``, so they round-trip through
    the catalog and into ``doc_content_hash`` untouched. The result is validated before it is
    returned -- an invalid document must never leave this module.
    """
    entry = resolve_defaults(class_name)
    envelope = _override_bbox(entry["bbox"], bbox)
    layout = strategy or entry["nozzles"]

    doc: dict = {
        "bbox": {"lx": envelope[0], "ly": envelope[1], "lz": envelope[2]},
        "mass": float(mass) if mass is not None else _mass_for(entry, envelope),
        # None lets the compiler default the centre of gravity to the bbox centroid.
        "cog": [float(v) for v in cog] if cog is not None else None,
        "ifc_element_class": ifc_element_class or entry["ifc_element_class"],
        "cad_z_up": True,
        "ports": place_nozzles(envelope, nozzles, layout),
        "nozzle_layout": layout,
        "dexpi_class": class_table.resolve(class_name),
    }
    if tag:
        doc["tag"] = tag
    if dexpi_id:
        doc["dexpi_id"] = dexpi_id
    return validate_equipment_doc(doc)


def _mass_for(entry: dict, envelope: list[float]) -> float:
    if entry["density"] is None:
        return float(entry["mass"])
    return round(float(entry["density"]) * envelope[0] * envelope[1] * envelope[2], 3)


def _override_bbox(default: list[float], bbox: Any) -> list[float]:
    if bbox is None:
        return list(default)
    if isinstance(bbox, dict):
        return [
            float(bbox.get("lx", default[0])),
            float(bbox.get("ly", default[1])),
            float(bbox.get("lz", default[2])),
        ]
    values = [float(v) for v in bbox]
    if len(values) != 3:
        raise ValueError(f"bbox must be [lx, ly, lz] or {{lx, ly, lz}}, got {bbox!r}")
    return values
