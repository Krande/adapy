"""Resolve an object's :data:`ada.base.ifc_types.IFC_ENTITY_KEY` against the schema being written."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ada.base.ifc_types import IFC_ENTITY_KEY
from ada.config import logger


@dataclass(frozen=True)
class IfcEntityChoice:
    entity: str
    predefined_type: str | None = None
    #: Set with ``predefined_type == "USERDEFINED"``: the requested value the schema does not list.
    object_type: str | None = None

    def attributes(self) -> dict[str, Any]:
        """The ``PredefinedType``/``ObjectType`` attributes to create the entity with."""
        out: dict[str, Any] = {}
        if self.predefined_type:
            out["PredefinedType"] = self.predefined_type
        if self.object_type:
            out["ObjectType"] = self.object_type
        return out


def _schema_keys(f) -> list[str]:
    keys = [str(getattr(f, "schema_identifier", "") or "").upper(), str(f.schema or "").upper()]
    return [k for k in dict.fromkeys(keys) if k]


def _declaration(f, entity: str):
    import ifcopenshell.ifcopenshell_wrapper as wrapper

    schema = wrapper.schema_by_name(getattr(f, "schema_identifier", None) or f.schema)
    try:
        return schema.declaration_by_name(entity)
    except Exception:  # noqa: BLE001 - an unknown name raises a wrapper-specific error
        return None


def _is_element(decl) -> bool:
    cursor = decl
    while cursor is not None:
        if cursor.name() == "IfcElement":
            return True
        cursor = cursor.supertype()
    return False


def _predefined_items(decl) -> tuple[str, ...] | None:
    for attr in decl.all_attributes():
        if attr.name() == "PredefinedType":
            return tuple(attr.type_of_attribute().declared_type().enumeration_items())
    return None


def resolve_ifc_entity(metadata: dict | None, f) -> IfcEntityChoice | None:
    """The entity ``metadata`` asks for in the schema of ``f``, or None to write the default.

    A per-schema override replaces the keys it names. An entity that is not a concrete
    ``IfcElement`` subtype in the schema is ignored with a warning. A predefined type the
    entity's enumeration does not list is written as ``USERDEFINED`` with the value as
    ``ObjectType``.
    """
    spec = (metadata or {}).get(IFC_ENTITY_KEY)
    if not isinstance(spec, dict) or not spec.get("entity"):
        return None
    resolved = {k: spec.get(k) for k in ("entity", "predefined_type")}
    overrides = {str(k).upper(): v for k, v in (spec.get("schemas") or {}).items()}
    for key in _schema_keys(f):
        if isinstance(overrides.get(key), dict):
            resolved.update(overrides[key])
            break

    entity = str(resolved.get("entity") or "").strip()
    decl = _declaration(f, entity) if entity else None
    if decl is None or not hasattr(decl, "is_abstract") or decl.is_abstract() or not _is_element(decl):
        logger.warning("ifc-write: %r is not a concrete IfcElement in %s; writing the default entity", entity, f.schema)
        return None

    predefined = str(resolved.get("predefined_type") or "").strip().upper() or None
    items = _predefined_items(decl)
    if predefined is None or items is None:
        return IfcEntityChoice(decl.name())
    if predefined in items:
        return IfcEntityChoice(decl.name(), predefined)
    return IfcEntityChoice(decl.name(), "USERDEFINED", predefined)
