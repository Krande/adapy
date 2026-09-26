"""IFC -> ``attributes.json``: the properties a click on a row should show.

Written at PUBLISH time, once per subject, for the reasons ``ada.assets.attributes`` states: the
serving process then needs no ifcopenshell, and a selection costs a dictionary lookup rather than
opening a source file that may be hundreds of megabytes.

WHAT IS COLLECTED, AND WHY EACH PART.

* **own** -- the entity's direct attributes, by name. ``Name``/``Description``/``ObjectType``/
  ``Tag``/``PredefinedType`` cover what a person reads first, and ``GlobalId`` is here so the
  panel can show the identity the whole key grammar is built on without a second lookup. Read
  off the entity rather than through ``get_info()``: that expands the representation and
  placement graphs, which is right for a change hash (``ifc/index.py``) and wasteful here.
* **groups** -- ``IfcPropertySet``s, keyed by set name, WITH inheritance from the type object.
  Inherited because that is where a real model puts most of it: an ``IfcBeamType`` carries the
  profile and the grade, and a panel that showed only instance-level sets would be empty for the
  ordinary case. ``should_inherit=True`` is ifcopenshell's own default and this relies on it
  rather than re-implementing the type walk.
* **quantities** -- ``IfcElementQuantity``s, kept apart from the property sets so a consumer after
  numbers does not have to guess which group holds them by name.
* **material** -- folded into ``own`` as a single readable string, not a group. The association is
  a graph (layer sets, profile sets, constituent sets) and rendering it faithfully is a panel's
  job, not a manifest's; the name is what a selection actually asks for.

Values are coerced to JSON primitives HERE rather than at write time. ifcopenshell hands back
entity instances for a property whose value is a reference, and ``json.dumps`` on one raises --
after the publish has already walked the whole file, which is the most expensive place to fail.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import ifcopenshell
import ifcopenshell.util.element

from ada.assets.attributes import NodeAttributes

__all__ = ["node_attributes", "attributes_for_nodes"]

#: Direct attributes worth showing, in the order a reader wants them. Absent ones are skipped, so
#: a class that does not define one (``Tag`` on a spatial container, say) costs nothing.
_OWN_ATTRIBUTES = ("Name", "Description", "ObjectType", "PredefinedType", "Tag", "GlobalId")

#: How deep a nested value is followed before it is rendered as text. Property values are
#: primitives in practice; the guard is for the pathological case, not the normal one.
_MAX_DEPTH = 4


def _primitive(value: Any, depth: int = 0) -> Any:
    """A JSON-safe rendering of one property value.

    ifcopenshell returns python primitives for most of them, entity instances for references, and
    tuples for aggregates. An entity becomes its own best label rather than a repr, because a repr
    carries a STEP line number that changes on every re-export and means nothing to a reader.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        if depth >= _MAX_DEPTH:
            return str(value)
        return [_primitive(v, depth + 1) for v in value]
    if isinstance(value, Mapping):
        if depth >= _MAX_DEPTH:
            return str(value)
        return {str(k): _primitive(v, depth + 1) for k, v in value.items()}
    if isinstance(value, ifcopenshell.entity_instance):
        return _entity_label(value)
    return str(value)


def _entity_label(entity: ifcopenshell.entity_instance) -> str:
    name = getattr(entity, "Name", None)
    return f"{entity.is_a()} {name}" if name else entity.is_a()


def _material_label(product: ifcopenshell.entity_instance) -> str | None:
    """The material association as one line, or None.

    ``should_skip_usage`` because the USAGE is the wrong object to name: an
    ``IfcMaterialProfileSetUsage`` has no ``Name`` of its own, so asking it yields its class where
    the set behind it says ``IPE200``. The usage records how the set is applied (which is a
    geometry concern), never what it is.

    A layer set or profile set names its own constituents; joining their names is what a reader
    would write down. A set with no usable names degrades to its class rather than to an empty
    string -- "there is a material and it is an IfcMaterialLayerSet" is information, "" is not.
    """
    try:
        material = ifcopenshell.util.element.get_material(product, should_skip_usage=True)
    except Exception:  # noqa: BLE001 - a malformed association must not fail a publish
        return None
    if material is None:
        return None
    name = getattr(material, "Name", None)
    if name:
        return str(name)
    for attr in ("Materials", "MaterialLayers", "MaterialProfiles", "MaterialConstituents"):
        members = getattr(material, attr, None) or ()
        labels = [str(n) for n in (_member_name(m) for m in members) if n]
        if labels:
            return ", ".join(labels)
    return material.is_a()


def _member_name(member: Any) -> str | None:
    inner = getattr(member, "Material", None)
    name = getattr(inner, "Name", None) or getattr(member, "Name", None)
    return str(name) if name else None


def node_attributes(product: ifcopenshell.entity_instance) -> NodeAttributes:
    """One product's facts. Never raises: a publish must not fail on a property it cannot read."""
    own: dict[str, Any] = {}
    for attr in _OWN_ATTRIBUTES:
        value = getattr(product, attr, None)
        if value is None or value == "":
            continue
        own[attr] = _primitive(value)

    material = _material_label(product)
    if material:
        own["Material"] = material

    groups = _read_psets(product, psets_only=True)
    quantities = _read_psets(product, qtos_only=True)

    return NodeAttributes(kind=product.is_a(), own=own, groups=groups, quantities=quantities)


def _read_psets(product: ifcopenshell.entity_instance, **kind: bool) -> dict[str, dict[str, Any]]:
    try:
        raw = ifcopenshell.util.element.get_psets(product, **kind)
    except Exception:  # noqa: BLE001 - one unreadable set must not cost the whole publish
        return {}
    out: dict[str, dict[str, Any]] = {}
    for set_name, props in (raw or {}).items():
        if not isinstance(props, Mapping):
            continue
        rendered = {
            str(k): _primitive(v)
            # `id` is ifcopenshell's own marker for which entity the set came from, not a
            # property of the thing. It is a STEP line number, so keeping it would put a value
            # that changes on every re-export into a document a reader diffs.
            for k, v in props.items()
            if k != "id" and v is not None and v != ""
        }
        if rendered:
            out[str(set_name)] = rendered
    return out


def attributes_for_nodes(f: ifcopenshell.file, node_ids: Iterable[str]) -> dict[str, NodeAttributes]:
    """``{GlobalId: NodeAttributes}`` for the nodes a subject covers.

    Keyed by GlobalId because that is what ``hierarchy.json`` carries as ``id``; a node the file
    no longer holds is skipped rather than raised, which is the same tolerance the sweep index
    has for a tree that moved under a re-export.
    """
    out: dict[str, NodeAttributes] = {}
    for node_id in node_ids:
        try:
            product = f.by_guid(node_id)
        except Exception:  # noqa: BLE001 - RuntimeError in ifcopenshell; absent is not a fault
            continue
        if product is None:
            continue
        out[node_id] = node_attributes(product)
    return out
