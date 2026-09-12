"""Turn a parsed DEXPI P&ID into a procedural document the 3D engine can build.

This module is the import path callers and tests have always used; the implementation lives in
:mod:`ada.cadit.dexpi.read.procedural`, split by concern:

* :mod:`~ada.cadit.dexpi.read.procedural.reports` -- what the import could not carry;
* :mod:`~ada.cadit.dexpi.read.procedural.resolve` -- endpoints, identity, segment and signal specs;
* :mod:`~ada.cadit.dexpi.read.procedural.synthesize` -- the equipment placed, real and synthesised;
* :mod:`~ada.cadit.dexpi.read.procedural.layout` -- layout rules, grouping, site terminals;
* :mod:`~ada.cadit.dexpi.read.procedural.merge` -- the branched fold and the base-document union;
* the package itself composes them into :func:`dexpi_to_resolved`,
  :func:`resolved_to_procedural_doc` and :func:`dexpi_to_procedural_doc`.

Every name the old single module defined is re-exported here, explicitly, so nothing that
imported from this path has to change.
"""

from __future__ import annotations

from .procedural import (
    DexpiImportReport,
    ImportIssue,
    InlineComponents,
    ResolvedDexpi,
    _copy_segment,
    default_layout_rules,
    dexpi_import_report,
    dexpi_to_procedural_doc,
    dexpi_to_resolved,
    resolved_to_procedural_doc,
)
from .procedural.layout import (
    _group_by_connectivity,
    _layout_rules,
    _no_none,
    _place_site_terminals,
    _restore_base_placements,
    _space_of,
    _stamp_equipment_metadata,
)
from .procedural.merge import _fold_branch_groups, _merge_systems
from .procedural.resolve import (
    _component_metadata,
    _descendants,
    _Endpoint,
    _Index,
    _off_page_direction,
    _resolve_endpoint,
    _routable_segments,
    _segment_spec,
    _SegmentSpec,
    _signal_endpoint,
    _signal_specs,
    _system_name,
    _system_type,
)
from .procedural.synthesize import (
    _chambers,
    _equipment_metadata,
    _equipment_names,
    _inline_equipment,
    _instrument_equipment,
    _instrument_ifc,
    _junction_equipment,
    _layout_item,
)

__all__ = [
    "DexpiImportReport",
    "ImportIssue",
    "InlineComponents",
    "default_layout_rules",
    "dexpi_import_report",
    "ResolvedDexpi",
    "dexpi_to_procedural_doc",
    "dexpi_to_resolved",
    "resolved_to_procedural_doc",
    # Implementation names, kept reachable from this path.
    "_Endpoint",
    "_Index",
    "_SegmentSpec",
    "_chambers",
    "_component_metadata",
    "_copy_segment",
    "_descendants",
    "_equipment_metadata",
    "_equipment_names",
    "_fold_branch_groups",
    "_group_by_connectivity",
    "_inline_equipment",
    "_instrument_equipment",
    "_instrument_ifc",
    "_junction_equipment",
    "_layout_item",
    "_layout_rules",
    "_merge_systems",
    "_no_none",
    "_off_page_direction",
    "_place_site_terminals",
    "_resolve_endpoint",
    "_restore_base_placements",
    "_routable_segments",
    "_segment_spec",
    "_signal_endpoint",
    "_signal_specs",
    "_space_of",
    "_stamp_equipment_metadata",
    "_system_name",
    "_system_type",
]
