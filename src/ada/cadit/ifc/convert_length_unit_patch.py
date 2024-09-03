# IfcPatch - IFC patching utiliy
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of IfcPatch.
#
# IfcPatch is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IfcPatch is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IfcPatch.  If not, see <http://www.gnu.org/licenses/>.

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.owner.settings
import ifcopenshell.util.element
import ifcopenshell.util.pset
import ifcopenshell.util.unit

wrap = ifcopenshell.ifcopenshell_wrapper


def get_base_type_name(content_type: wrap.named_type | wrap.type_declaration) -> wrap.type_declaration | None:
    cur_decl = content_type
    while hasattr(cur_decl, "declared_type") is True:
        cur_decl = cur_decl.declared_type()
        if hasattr(cur_decl, "name") is False:
            continue
        if cur_decl.name() == "IfcLengthMeasure":
            return cur_decl

    if isinstance(cur_decl, wrap.aggregation_type):
        res = cur_decl.type_of_element()
        cur_decl = res.declared_type()
        if hasattr(cur_decl, "name") and cur_decl.name() == "IfcLengthMeasure":
            return cur_decl
        while hasattr(cur_decl, "declared_type") is True:
            cur_decl = cur_decl.declared_type()
            if hasattr(cur_decl, "name") is False:
                continue
            if cur_decl.name() == "IfcLengthMeasure":
                return cur_decl

    return None


class Patcher:
    def __init__(self, src, file, logger, unit="METERS"):
        """Converts the length unit of a model to the specified unit

        Allowed metric units include METERS, MILLIMETERS, CENTIMETERS, etc.
        Allowed imperial units include INCHES, FEET, MILES.

        :param unit: The name of the desired unit.
        :type unit: str

        Example:

        .. code:: python

            # Convert to millimeters
            ifcpatch.execute({"input": "input.ifc", "file": model, "recipe": "ConvertLengthUnit", "arguments": ["MILLIMETERS"]})

            # Convert to feet
            ifcpatch.execute({"input": "input.ifc", "file": model, "recipe": "ConvertLengthUnit", "arguments": ["FEET"]})
        """
        self.src = src
        self.file: ifcopenshell.file = file
        self.logger = logger
        self.unit = unit
        self.file_patched: ifcopenshell.file

    def patch(self):
        self.file_patched = ifcopenshell.api.run("project.create_file", version=self.file.schema)
        if self.file.schema == "IFC2X3":
            user = self.file_patched.add(self.file.by_type("IfcProject")[0].OwnerHistory.OwningUser)
            application = self.file_patched.add(self.file.by_type("IfcProject")[0].OwnerHistory.OwningApplication)
            old_get_user = ifcopenshell.api.owner.settings.get_user
            old_get_application = ifcopenshell.api.owner.settings.get_application
            ifcopenshell.api.owner.settings.get_user = lambda ifc: user
            ifcopenshell.api.owner.settings.get_application = lambda ifc: application

        # Copy all elements from the original file to the patched file
        for el in self.file:
            self.file_patched.add(el)

        prefix = "MILLI" if self.unit == "MILLIMETERS" else None
        new_length = ifcopenshell.api.run("unit.add_si_unit", self.file_patched, unit_type="LENGTHUNIT", prefix=prefix)
        unit_assignment = ifcopenshell.util.unit.get_unit_assignment(self.file)
        old_length = [u for u in unit_assignment.Units if getattr(u, "UnitType", None) == "LENGTHUNIT"][0]

        schema = wrap.schema_by_name(self.file.schema)
        # Traverse all elements and their nested attributes in the file and convert them
        for element in self.file_patched:
            entity = schema.declaration_by_name(element.is_a())
            attrs = entity.all_attributes()
            for i, (attr, val, is_derived) in enumerate(zip(attrs, list(element), entity.derived())):
                if is_derived:
                    continue
                # Get all methods and attributes of the element
                attr_type = attr.type_of_attribute()
                base_type = get_base_type_name(attr_type)
                if base_type is None:
                    continue
                if val is None:
                    continue
                if isinstance(val, tuple):
                    new_el = [ifcopenshell.util.unit.convert_unit(v, old_length, new_length) for v in val]
                    setattr(element, attr.name(), tuple(new_el))
                else:
                    new_el = ifcopenshell.util.unit.convert_unit(val, old_length, new_length)
                    # set the new value
                    setattr(element, attr.name(), new_el)

        if self.file.schema == "IFC2X3":
            ifcopenshell.api.owner.settings.get_user = old_get_user
            ifcopenshell.api.owner.settings.get_application = old_get_application
