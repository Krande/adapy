import ifcopenshell.util.element

from ada.base.units import Units
from ada.cadit.ifc.convert_length_unit_patch import Patcher


def convert_units(units: Units, f: ifcopenshell.file):
    units_str = "MILLIMETERS" if units == Units.MM else "METERS"

    task = Patcher(src=None, file=f, logger=None, unit=units_str)
    task.patch()

    return task.file_patched
