"""Per-job-kind format handlers for the conversion worker.

Importing this package registers every built-in handler (in the order the old dispatch chain
tested them) plus the registry-backed converter fallback. See :mod:`.registry` for the contract.
"""

from __future__ import annotations

from . import (
    component,
    convert,
    engine_build,
    equipment,
    fea,
    parity,
    plugin,
    procedural_build,
    procedural_detail,
    procedural_export,
    utility,
)
from .registry import (
    FormatHandler,
    JobContext,
    SourceFormatHandler,
    SyntheticFormatHandler,
    UnknownJobKind,
    handlers,
    register,
    registered_kinds,
    resolve,
)

# Synthetic (sourceless) kinds — dispatched before any source download.
register(component.ComponentBuildHandler())
register(procedural_build.ProceduralBuildHandler())
register(plugin.PluginJobHandler())
register(procedural_detail.ProceduralDetailHandler())
register(procedural_detail.ProceduralRelocationsHandler())
register(procedural_export.ProceduralExportXlsxHandler())
register(procedural_export.ProceduralExportModelHandler())
register(procedural_export.ProceduralImportXlsxHandler())
register(equipment.EquipmentBboxHandler())
register(engine_build.ProceduralEngineBuildHandler())
# Source-backed kinds that are not registry conversions.
register(utility.UtilityHandler())
register(fea.FeaArtefactsHandler())
register(fea.FeaMetaHandler())
register(parity.ParityHandler())
# Everything else: the ConverterRegistry-backed convert() path.
register(convert.ConvertHandler(), fallback=True)

__all__ = [
    "FormatHandler",
    "JobContext",
    "SourceFormatHandler",
    "SyntheticFormatHandler",
    "UnknownJobKind",
    "handlers",
    "register",
    "registered_kinds",
    "resolve",
]
