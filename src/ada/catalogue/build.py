"""Catalogue rows (``GET /api/scopes/{scope}/catalogue/...``) as adapy objects.

A SECTION ROW's ``section`` is ``{type, dims, units, gtype?, source?}``: ``type`` an adapy section
type (``ada.sections.categories.BaseTypes`` value) or ``PLATE``, ``dims`` in adapy's ``Section``
parameter names. A provider may send only the dimensions that define the profile (one wall
thickness ``t`` for a hollow section, a diameter ``d`` for a tube); :func:`section_dims` completes
the slots adapy's geometry reads, the way ``ada.sections.string_to_section`` fills them from a
section string, and refuses a row that lacks a defining dimension.

A MATERIAL ROW's ``material.effective`` is the provider's values with an admin's override merged
over them, in SI units. What it does not say is taken from the family's defaults
(:data:`FAMILY_DEFAULTS`) and LISTED in the material's metadata (``catalogue.defaulted``) -- or
refused, with ``strict``.

Every object built here carries ``metadata["catalogue"]`` (:data:`CATALOGUE_KEY`): the exact ``ref``,
its collection and provider, and for a section its ``gtype`` -- what lets a writer emit the source's
own reference (``ada.cadit.e3d.write_mac``) and a whitelist check (``ada.catalogue.whitelist``) prove
where an object came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ada.base.units import Units

#: The metadata key every catalogue-built object carries.
CATALOGUE_KEY = "catalogue"

#: Per material family, what a property defaults to when neither the provider nor an override says.
#: Strengths have no default: a grade the family table does not know leaves them unset (``Metal``)
#: or refused (``strict``).
FAMILY_DEFAULTS: dict[str, dict[str, float]] = {
    "carbon_steel": {"E": 2.1e11, "v": 0.3, "rho": 7850.0, "alpha": 1.2e-5, "zeta": 0.03},
    "stainless_steel": {"E": 2.0e11, "v": 0.3, "rho": 7900.0, "alpha": 1.6e-5, "zeta": 0.03},
    "aluminium": {"E": 7.0e10, "v": 0.33, "rho": 2700.0, "alpha": 2.3e-5, "zeta": 0.03},
    "other": {"E": 2.1e11, "v": 0.3, "rho": 7850.0, "alpha": 1.2e-5, "zeta": 0.03},
}

_LENGTH_SCALE = {"mm": 0.001, "m": 1.0}


class CatalogueError(ValueError):
    """A catalogue entry adapy cannot build as asked."""


def ref_name(ref: str) -> str:
    """The name an object built from ``ref`` gets: its last path segment (``/SPEC/HEA200`` ->
    ``HEA200``). The full ref is in the metadata."""
    tail = ref.rstrip("/").rsplit("/", 1)[-1]
    return tail or ref.strip("/") or ref


def _meta(row: dict, **extra: Any) -> dict:
    out = {"ref": row["ref"], "collection": row.get("collection"), "provider": row.get("provider")}
    out.update({k: v for k, v in extra.items() if v is not None})
    return {CATALOGUE_KEY: out}


def _scaled(dims: dict, units: str) -> dict[str, float]:
    scale = _LENGTH_SCALE.get((units or "mm").lower())
    if scale is None:
        raise CatalogueError(f"unknown length unit {units!r} in a catalogue section")
    out: dict[str, float] = {}
    for k, v in (dims or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[k] = float(v) * scale
    return out


def section_dims(stype: str, dims: dict[str, float]) -> dict[str, float]:
    """``dims`` completed for adapy's ``Section`` of ``stype`` (raises :class:`CatalogueError` when a
    defining dimension is missing). Lengths in metres."""
    d = dict(dims)
    t = d.pop("t", None)
    diameter = d.pop("d", None)

    def need(*keys: str) -> None:
        missing = [k for k in keys if d.get(k) is None]
        if missing:
            raise CatalogueError(f"a {stype} section needs {', '.join(missing)}; the catalogue row has {sorted(dims)}")

    if stype in ("I", "UNP", "T"):
        need("h", "w_top", "t_w", "t_ftop")
        d.setdefault("w_btn", d["w_top"])
        d.setdefault("t_fbtn", d["t_ftop"])
    elif stype == "BOX":
        for k in ("t_w", "t_ftop"):
            if d.get(k) is None and t is not None:
                d[k] = t
        if d.get("w_top") is None and d.get("h") is not None:
            d["w_top"] = d["h"]  # a square hollow section names one side
        need("h", "w_top", "t_w", "t_ftop")
        d.setdefault("w_btn", d["w_top"])
        d.setdefault("t_fbtn", d["t_ftop"])
    elif stype == "HP":
        if d.get("t_w") is None and t is not None:
            d["t_w"] = t
        need("h", "w_top", "t_w")
        d.setdefault("w_btn", d["w_top"])
        d.setdefault("t_ftop", d["t_w"])
        d.setdefault("t_fbtn", d["t_w"])
    elif stype == "TUB":
        if d.get("r") is None and diameter is not None:
            d["r"] = diameter / 2
        if d.get("wt") is None and t is not None:
            d["wt"] = t
        need("r", "wt")
    elif stype == "CIRC":
        if d.get("r") is None and diameter is not None:
            d["r"] = diameter / 2
        need("r")
    elif stype == "FB":
        if d.get("w_top") is None and t is not None:
            d["w_top"] = t
        need("h", "w_top")
        d.setdefault("w_btn", d["w_top"])
    else:
        raise CatalogueError(f"section type {stype!r} is not one adapy builds from dimensions")
    return d


def section_from_row(row: dict, *, name: str | None = None):
    """An ``ada.Section`` (metres) from a catalogue section row."""
    from ada import Section
    from ada.sections.categories import BaseTypes

    spec = row.get("section")
    if not isinstance(spec, dict) or not spec.get("type"):
        raise CatalogueError(
            f"{row['ref']} has no section dimensions in the catalogue (its provider could not map its "
            "parameters); its raw parameters are in the row's 'params'"
        )
    stype = str(spec["type"]).upper()
    if stype == "PLATE":
        raise CatalogueError(f"{row['ref']} is a plate spec; read it with ScopeCatalogue.plate()")
    dims = section_dims(stype, _scaled(spec.get("dims") or {}, spec.get("units") or "mm"))
    meta = _meta(row, gtype=row.get("gtype") or spec.get("gtype"), spec=row.get("spec"), source=spec.get("source"))
    return Section(
        name or ref_name(row["ref"]),
        sec_type=BaseTypes.from_str(stype),
        units=Units.M,
        metadata=meta,
        **{k: v for k, v in dims.items() if k in ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt")},
    )


@dataclass(frozen=True)
class CataloguePlate:
    """A plate spec of the catalogue: its thickness (metres) and the metadata a ``Plate`` built with
    it should carry (``ada.Plate(..., t=p.t, metadata=p.metadata)``)."""

    ref: str
    t: float
    metadata: dict = field(default_factory=dict)


def plate_from_row(row: dict) -> CataloguePlate:
    spec = row.get("section")
    if not isinstance(spec, dict) or str(spec.get("type") or "").upper() != "PLATE":
        raise CatalogueError(f"{row['ref']} is not a plate spec with a thickness in the catalogue")
    dims = _scaled(spec.get("dims") or {}, spec.get("units") or "mm")
    if dims.get("t") is None:
        raise CatalogueError(f"{row['ref']} names no plate thickness")
    return CataloguePlate(
        ref=row["ref"], t=dims["t"], metadata=_meta(row, spec=row.get("spec"), source=spec.get("source"))
    )


def material_from_row(row: dict, *, name: str | None = None, strict: bool = False):
    """An ``ada.Material`` from a catalogue material row's merged (``effective``) properties.

    A carbon steel is adapy's ``CarbonSteel`` (its grade the row's, or ``S355``'s table when the row
    names a grade the table has); any other family a ``Metal``. A property neither side gave is the
    family's default, listed in ``metadata["catalogue"]["defaulted"]``; with ``strict`` it is refused,
    as are missing strengths."""
    from ada import Material
    from ada.materials.metals import CarbonSteel
    from ada.materials.metals.base_models import Metal

    effective = dict(((row.get("material") or {}).get("effective")) or {})
    family = effective.get("family") or "other"
    if family not in FAMILY_DEFAULTS:
        family = "other"
    defaults = FAMILY_DEFAULTS[family]
    values: dict[str, float] = {}
    defaulted: list[str] = []
    for key, default in defaults.items():
        if effective.get(key) is not None:
            values[key] = float(effective[key])
        else:
            values[key] = default
            defaulted.append(key)
    grade = effective.get("grade")
    sig_y, sig_u = effective.get("sig_y"), effective.get("sig_u")
    if family == "carbon_steel" and grade in CarbonSteel.GRADES:
        table = CarbonSteel.GRADES[grade]
        if sig_y is None:
            sig_y = table["sigy"]
            defaulted.append("sig_y")
        if sig_u is None:
            sig_u = table["sigu"]
            defaulted.append("sig_u")
    if strict:
        missing = [k for k in defaulted] + [k for k, v in (("sig_y", sig_y), ("sig_u", sig_u)) if v is None]
        if missing:
            raise CatalogueError(
                f"{row['ref']} has no {', '.join(sorted(set(missing)))} in the catalogue; an admin sets them as an "
                "override in ada studio (Admin > Providers > Reference data)"
            )
    if family == "carbon_steel" and sig_y is not None and sig_u is not None:
        model = CarbonSteel(
            grade=grade or "S355",
            E=values["E"],
            rho=values["rho"],
            v=values["v"],
            alpha=values["alpha"],
            zeta=values["zeta"],
            sig_y=float(sig_y),
            sig_u=float(sig_u),
        )
    else:
        model = Metal(
            E=values["E"],
            rho=values["rho"],
            sig_y=None if sig_y is None else float(sig_y),
            sig_u=None if sig_u is None else float(sig_u),
            v=values["v"],
            zeta=values["zeta"],
            alpha=values["alpha"],
        )
    meta = _meta(
        row,
        family=family,
        grade=grade,
        defaulted=sorted(set(defaulted)) or None,
        source=(row.get("material") or {}).get("source"),
    )
    return Material(name or ref_name(row["ref"]), mat_model=model, metadata=meta, units=Units.M)
