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
import ifcopenshell.util.pset
import ifcopenshell.util.element
import ifcopenshell.util.unit
import ifcopenshell.express.schema

wrap = ifcopenshell.ifcopenshell_wrapper


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

        project_super = (
            ifcopenshell.ifcopenshell_wrapper.schema_by_name(self.file.schema)
            .declaration_by_name("IfcProject")
            .supertype()
            .name()
        )
        if project_super == "IfcObject":
            project_super = "IfcProject"

        schema = wrap.schema_by_name(self.file.schema)
        decl = schema.declarations()
        length_type = [x for x in decl if x.name() == "IfcLengthMeasure"][0]
        # Find all classes that are using IfcLengthMeasure
        length_classes = [x for x in decl if x == length_type]
        length_class = length_classes[0]
        res = self.file.wrapped_data.get_total_inverses(length_class)
        for element in filter(lambda inst: not inst.is_a(project_super), self.file):
            self.file_patched.add(element)

        unit_assignment = ifcopenshell.util.unit.get_unit_assignment(self.file_patched)

        prefix = "MILLI" if self.unit == "MILLIMETERS" else None
        new_length = ifcopenshell.api.run("unit.add_si_unit", self.file_patched, unit_type="LENGTHUNIT", prefix=prefix)
        self.file_patched.add(new_length)

        old_length = [u for u in unit_assignment.Units if getattr(u, "UnitType", None) == "LENGTHUNIT"][0]

        for elem in self.file.by_type("IfcRepresentationItem"):
            # walk element properties
            elem: ifcopenshell.entity_instance
            for sub_prop in self.file.traverse(elem):
                print(sub_prop)
                ifcopenshell.util.unit.convert_unit(elem, old_length, new_length)
            # ifcopenshell.util.unit.convert(elem, None, old_length, None, new_length)

        for inverse in self.file_patched.get_inverse(old_length):
            ifcopenshell.util.element.replace_attribute(inverse, old_length, new_length)

        self.file_patched.remove(old_length)

        if self.file.schema == "IFC2X3":
            ifcopenshell.api.owner.settings.get_user = old_get_user
            ifcopenshell.api.owner.settings.get_application = old_get_application
