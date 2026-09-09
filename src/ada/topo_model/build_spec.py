"""Everything the procedural engine needs to turn a resolved process model into 3D.

One object rather than a parameter list, because the list was the symptom: ``ada.from_dexpi`` grew
to thirteen arguments and roughly half of them said nothing about DEXPI at all -- they described how
to *build*. Reading a P&ID and building a plant are two jobs, and the arguments for the second one
belong together.

The spec **composes** the types that already exist rather than flattening them.
:class:`~ada.topo_model.layout.LayoutRules` is a good object with its own defaults and its own
meaning; copying its fields in here would leave two places to change and one of them wrong. Same for
the design ruleset, which is resolved from a slug by
:func:`~ada.topo_model.design_rulesets.resolve_design_rules`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .layout import LayoutRules

__all__ = ["ProceduralBuildSpec"]


@dataclass
class ProceduralBuildSpec:
    """How to build a 3D model from a resolved process model.

    Every field is about *placement and fabrication* -- where equipment stands, what structure
    carries it, how the runs get from A to B. Nothing here describes the process model itself; that
    is settled by the time this is used.
    """

    #: Deck bounds and pitch for the generated layout. Pass an explicit ``deck_height``: the pitch
    #: is uniform across a plan, so leaving it unset makes every deck as tall as the tallest item.
    layout: LayoutRules = field(default_factory=LayoutRules)

    #: Design ruleset slug (or an already-resolved ruleset) governing routing and penetrations.
    design_rules: str = "standard"

    #: Structural blueprint that generates the decks' steel. ``"none"`` places equipment and routes
    #: without building structure.
    blueprint: Literal["steel_stru", "none"] = "steel_stru"

    #: Route the systems. ``False`` places the equipment and leaves the runs unrouted.
    route: bool = True

    #: Feed the router's failures back into the layout: build, compute the equipment moves that
    #: would clear the runs that did not route, apply them, build again. ``True`` means two passes;
    #: an int sets the pass count. Off by default because it changes where equipment stands.
    relocate: bool | int = False

    #: An existing procedural document whose equipment placements win over the generated ones --
    #: how a re-import keeps positions that were already corrected by hand.
    base_doc: dict | None = None

    #: Level of detail for the compiled geometry.
    lod: Literal["sim", "detail"] = "sim"

    #: Detailing engine slug, and its options. ``None`` leaves joints undetailed.
    detailing: str | None = None
    detailing_options: dict | None = None

    def with_(self, **overrides) -> "ProceduralBuildSpec":
        """A copy with ``overrides`` applied -- for varying one knob without restating the rest."""
        import dataclasses

        return dataclasses.replace(self, **overrides)
