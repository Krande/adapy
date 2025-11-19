"""
ACIS SAT Parser Module

A comprehensive parser for ACIS SAT files that converts ACIS geometry entities
to adapy's internal geometry representations based on the STEP standard.
"""

from ada.cadit.sat.parser.acis_entities import *
from ada.cadit.sat.parser.parser import AcisSatParser
from ada.cadit.sat.parser.converter import AcisToAdaConverter

__all__ = [
    "AcisSatParser",
    "AcisToAdaConverter",
]
