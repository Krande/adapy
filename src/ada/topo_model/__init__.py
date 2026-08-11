"""ada.topo_model — a small, generic demonstration of the ``ada.topology``
procedural engine: space boxes in, a steel structure out.

Start with :func:`build_topo_model` for the one-liner happy path, and read
:class:`SteelStru` as the reference for authoring your own blueprint.
"""

from .blueprint import SteelStru
from .blueprint_catalog import (
    procedural_blueprint_specs,
    register_procedural_blueprint,
)
from .engine_catalog import (
    procedural_engine_specs,
    register_procedural_engine_capabilities,
)
from .jacket import JacketStru
from .build import (
    build_routing_grid,
    build_topo_model,
    build_topo_model_with_systems,
    make_space_boxes,
)
from .builder import ProceduralBuilder
from .catalog import EquipmentType, ProceduralCatalog, SystemTemplate
from .cell_types import (
    procedural_cell_type_specs,
    procedural_opening_type_specs,
    register_procedural_cell_type,
    register_procedural_opening_type,
)
from .excel import ProceduralModelMeta
from .design_rulesets import (
    DEFAULT_DESIGN_RULESET,
    DESIGN_RULESETS,
    design_ruleset_specs,
    resolve_design_rules,
)
from .equipment import create_pump, create_switchboard, create_tank
from .penetration import (
    PenetrationBlueprintBase,
    StandardPenetrations,
    standard_design_rules,
    standard_penetration_modeller,
)
from .relocate import propose_relocations, run_self_collides
from .templates import procedural_template_specs, register_procedural_template

__all__ = [
    "DEFAULT_DESIGN_RULESET",
    "DESIGN_RULESETS",
    "EquipmentType",
    "JacketStru",
    "PenetrationBlueprintBase",
    "ProceduralBuilder",
    "ProceduralCatalog",
    "ProceduralModelMeta",
    "StandardPenetrations",
    "SteelStru",
    "SystemTemplate",
    "build_routing_grid",
    "build_topo_model",
    "build_topo_model_with_systems",
    "create_pump",
    "create_switchboard",
    "create_tank",
    "design_ruleset_specs",
    "make_space_boxes",
    "procedural_blueprint_specs",
    "procedural_cell_type_specs",
    "procedural_engine_specs",
    "procedural_opening_type_specs",
    "procedural_template_specs",
    "propose_relocations",
    "register_procedural_blueprint",
    "register_procedural_cell_type",
    "register_procedural_engine_capabilities",
    "register_procedural_opening_type",
    "register_procedural_template",
    "resolve_design_rules",
    "run_self_collides",
    "standard_design_rules",
    "standard_penetration_modeller",
]
