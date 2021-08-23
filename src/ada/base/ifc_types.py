from enum import Enum, auto


class IfcType(Enum):
    # TODO: Check to see if this enum can be grabbed from ifcopenshell directly
    # Non-physical Container Top Element
    IfcSite = auto()
    # Non-physical Container SubElements
    IfcBuilding = auto()
    IfcSpatialZone = auto()
    IfcBuildingStorey = auto()
    # Is this valid though?
    IfcSpace = auto()
