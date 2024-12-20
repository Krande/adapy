from typing import Any, Iterable

import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.unit

from ada.base.units import Units
from ada.cadit.ifc.convert_length_unit_patch import Patcher


def convert_units(units: Units, f: ifcopenshell.file):
    units_str = "MILLIMETERS" if units == Units.MM else "METERS"

    task = Patcher(src=None, file=f, logger=None, unit=units_str)
    task.patch()

    return task.file_patched


def is_attr_type(
    content_type: ifcopenshell.ifcopenshell_wrapper.named_type | ifcopenshell.ifcopenshell_wrapper.type_declaration,
    ifc_unit_type_name: str,
) -> ifcopenshell.ifcopenshell_wrapper.type_declaration | None:
    cur_decl = content_type
    while hasattr(cur_decl, "declared_type") is True:
        cur_decl = cur_decl.declared_type()
        if hasattr(cur_decl, "name") is False:
            continue
        if cur_decl.name() == ifc_unit_type_name:
            return cur_decl

    if isinstance(cur_decl, ifcopenshell.ifcopenshell_wrapper.aggregation_type):
        res = cur_decl.type_of_element()
        if hasattr(res, "declared_type") is False:
            # it's likely a list of lists situation
            subres = res.type_of_element()
            if hasattr(subres, "declared_type"):
                res = subres
        cur_decl = res.declared_type()
        if hasattr(cur_decl, "name") and cur_decl.name() == ifc_unit_type_name:
            return cur_decl
        while hasattr(cur_decl, "declared_type") is True:
            cur_decl = cur_decl.declared_type()
            if hasattr(cur_decl, "name") is False:
                continue
            if cur_decl.name() == ifc_unit_type_name:
                return cur_decl

    return None


def iter_element_and_attributes_per_type(
    ifc_file: ifcopenshell.file, attr_type_name: str
) -> Iterable[tuple[ifcopenshell.entity_instance, ifcopenshell.ifcopenshell_wrapper.attribute, Any, str]]:
    schema_map = {"IFC4X3": "IFC4X3_ADD2"}
    schema_name = schema_map.get(ifc_file.schema, ifc_file.schema)
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(schema_name)

    for element in ifc_file:
        entity = schema.declaration_by_name(element.is_a())
        attrs = entity.all_attributes()
        for i, (attr, val, is_derived) in enumerate(zip(attrs, list(element), entity.derived())):
            if is_derived:
                continue

            # Get all methods and attributes of the element
            attr_type = attr.type_of_attribute()
            base_type = is_attr_type(attr_type, attr_type_name)
            if base_type is None:
                continue

            if val is None:
                continue

            yield element, attr, val


def convert_list_values(value, old_length, new_length):
    for v in value:
        if isinstance(v, tuple):
            yield tuple([x for x in convert_list_values(v, old_length, new_length)])
        else:
            yield ifcopenshell.util.unit.convert_unit(v, old_length, new_length)


def convert_file_length_units(ifc_file: ifcopenshell.file, units: Units) -> ifcopenshell.file:
    """Converts all units in an IFC file to the specified target units. Returns a new file."""
    target_units = "MILLIMETERS" if units == Units.MM else "METERS"
    prefix = "MILLI" if target_units == "MILLIMETERS" else None

    # Copy all elements from the original file to the patched file
    file_patched = ifcopenshell.file.from_string(ifc_file.wrapped_data.to_string())

    unit_assignment = ifcopenshell.util.unit.get_unit_assignment(file_patched)

    old_length = [u for u in unit_assignment.Units if getattr(u, "UnitType", None) == "LENGTHUNIT"][0]
    new_length = ifcopenshell.api.run("unit.add_si_unit", file_patched, unit_type="LENGTHUNIT", prefix=prefix)

    # Traverse all elements and their nested attributes in the file and convert them
    for element, attr, val in iter_element_and_attributes_per_type(file_patched, "IfcLengthMeasure"):
        if isinstance(val, tuple):
            new_value = tuple(convert_list_values(val, old_length, new_length))
            setattr(element, attr.name(), new_value)
        else:
            new_value = ifcopenshell.util.unit.convert_unit(val, old_length, new_length)
            setattr(element, attr.name(), new_value)

    file_patched.remove(old_length)
    unit_assignment.Units = tuple([new_length, *unit_assignment.Units])

    return file_patched
