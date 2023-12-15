import ifcopenshell.api
import ifcopenshell.api.owner.settings
import ifcopenshell.util.pset
import ifcopenshell.util.element

from ada.base.units import Units


def convert_units(units: Units, file: ifcopenshell.file):
    units_str = "MILLIMETERS" if units == Units.MM else "METERS"
    unit = {"is_metric": True, "raw": units_str}

    file_patched = ifcopenshell.api.run("project.create_file", version=file.schema)
    if file.schema == "IFC2X3":
        user = file_patched.add(file.by_type("IfcProject")[0].OwnerHistory.OwningUser)
        application = file_patched.add(file.by_type("IfcProject")[0].OwnerHistory.OwningApplication)
        old_get_user = ifcopenshell.api.owner.settings.get_user
        old_get_application = ifcopenshell.api.owner.settings.get_application
        ifcopenshell.api.owner.settings.get_user = lambda ifc: user
        ifcopenshell.api.owner.settings.get_application = lambda ifc: application
    project = ifcopenshell.api.run("root.create_entity", file_patched, ifc_class="IfcProject")
    unit_assignment = ifcopenshell.api.run("unit.assign_unit", file_patched, **{"length": unit})

    # Is there a better way?
    for element in file.by_type("IfcGeometricRepresentationContext", include_subtypes=False):
        element.Precision = 1e-8

    # If we don't add openings first, they don't get converted
    for element in file.by_type("IfcOpeningElement"):
        file_patched.add(element)

    for element in file:
        file_patched.add(element)

    new_length = [u for u in unit_assignment.Units if getattr(u, "UnitType", None) == "LENGTHUNIT"][0]
    old_length = [
        u
        for u in file_patched.by_type("IfcProject")[1].UnitsInContext.Units
        if getattr(u, "UnitType", None) == "LENGTHUNIT"
    ][0]

    for inverse in file_patched.get_inverse(old_length):
        ifcopenshell.util.element.replace_attribute(inverse, old_length, new_length)

    file_patched.remove(old_length)
    file_patched.remove(project)

    if file.schema == "IFC2X3":
        ifcopenshell.api.owner.settings.get_user = old_get_user
        ifcopenshell.api.owner.settings.get_application = old_get_application

    return file_patched
