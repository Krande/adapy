"""The sections and materials a scope may build with, from ada studio -- provider-agnostic.

``scope_catalogue(scope)`` reads ``GET /api/scopes/{scope}/catalogue/sections`` and ``.../materials``
(the reference data the scope may read, merged across its collections, with an admin's material
overrides merged in) and builds ``ada.Section`` / ``ada.Material`` objects that remember the exact
catalogue ref they came from (``metadata["catalogue"]``). ``check_assembly`` is the whitelist mode;
``ada.cadit.e3d.write_mac.E3DWriter`` writes the remembered refs back.
"""

from .build import (
    CATALOGUE_KEY,
    FAMILY_DEFAULTS,
    CatalogueError,
    CataloguePlate,
    material_from_row,
    plate_from_row,
    ref_name,
    section_dims,
    section_from_row,
)
from .client import ScopeCatalogue, scope_catalogue
from .whitelist import CatalogueWhitelistError, Violation, catalogue_ref, check_assembly

__all__ = [
    "CATALOGUE_KEY",
    "FAMILY_DEFAULTS",
    "CatalogueError",
    "CataloguePlate",
    "CatalogueWhitelistError",
    "ScopeCatalogue",
    "Violation",
    "catalogue_ref",
    "check_assembly",
    "material_from_row",
    "plate_from_row",
    "ref_name",
    "scope_catalogue",
    "section_dims",
    "section_from_row",
]
