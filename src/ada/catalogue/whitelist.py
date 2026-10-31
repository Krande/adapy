"""Whitelist mode: does every beam and plate of a model use a section and material from the
catalogue?

For procedural and regular models built from a scope's catalogue (``ada.catalogue.scope_catalogue``)
-- so a model meant for a source system holds nothing that system does not offer. An object passes
when its section (a plate: the plate itself) and its material carry ``metadata["catalogue"]["ref"]``
and, given a catalogue, that ref is still in it (and a plate's thickness is still the spec's).

    violations = check_assembly(a, cat)            # a report
    check_assembly(a, cat, raise_on_violation=True)  # or refuse
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .build import CATALOGUE_KEY, CatalogueError

if TYPE_CHECKING:
    from .client import ScopeCatalogue


@dataclass(frozen=True)
class Violation:
    """One object that is not from the catalogue: ``what`` is ``section``, ``plate`` or
    ``material``; ``ref`` the catalogue ref it carries, if any."""

    object_name: str
    what: str
    ref: Optional[str]
    reason: str


class CatalogueWhitelistError(CatalogueError):
    def __init__(self, violations: list[Violation]):
        self.violations = violations
        head = "; ".join(f"{v.object_name} ({v.what}): {v.reason}" for v in violations[:10])
        more = f" and {len(violations) - 10} more" if len(violations) > 10 else ""
        super().__init__(f"{len(violations)} object(s) use what the catalogue does not offer: {head}{more}")


def catalogue_ref(obj) -> Optional[str]:
    """The catalogue ref an object (a ``Section``, ``Material`` or ``Plate``) was built from, or None."""
    meta = getattr(obj, "metadata", None) or {}
    entry = meta.get(CATALOGUE_KEY) if isinstance(meta, dict) else None
    return entry.get("ref") if isinstance(entry, dict) else None


def _collection(obj) -> Optional[str]:
    entry = (getattr(obj, "metadata", None) or {}).get(CATALOGUE_KEY) or {}
    return entry.get("collection") if isinstance(entry, dict) else None


def check_assembly(
    part, catalogue: "ScopeCatalogue | None" = None, *, raise_on_violation: bool = False
) -> list[Violation]:
    """Every beam and plate of ``part`` (an ``Assembly`` or any ``Part``) and below, checked. Returns
    the violations (empty: all from the catalogue); raises :class:`CatalogueWhitelistError` instead
    with ``raise_on_violation``."""
    from ada import Beam, Plate

    out: list[Violation] = []

    def check(obj_name: str, what: str, carrier, *, plate=None) -> None:
        ref = catalogue_ref(carrier)
        if ref is None:
            out.append(Violation(obj_name, what, None, "not built from the catalogue (no catalogue ref)"))
            return
        if catalogue is None:
            return
        coll = _collection(carrier)
        if what == "material":
            if not catalogue.has_material(ref, coll):
                out.append(Violation(obj_name, what, ref, "the catalogue no longer offers it"))
            return
        if what == "section":
            if not catalogue.has_section(ref, coll):
                out.append(Violation(obj_name, what, ref, "the catalogue no longer offers it"))
            return
        try:
            spec = catalogue.plate(ref, collection=coll)
        except CatalogueError as exc:
            out.append(Violation(obj_name, what, ref, str(exc)))
            return
        from ada.base.units import Units

        t = float(plate.t) * (0.001 if plate.units == Units.MM else 1.0)
        if abs(t - spec.t) > 1e-6:
            out.append(Violation(obj_name, what, ref, f"{t} m thick, the spec is {spec.t} m"))

    for obj in part.get_all_physical_objects(by_type=Beam):
        check(obj.name, "section", obj.section)
        check(obj.name, "material", obj.material)
    for obj in part.get_all_physical_objects(by_type=Plate):
        check(obj.name, "plate", obj, plate=obj)
        check(obj.name, "material", obj.material)

    if out and raise_on_violation:
        raise CatalogueWhitelistError(out)
    return out
