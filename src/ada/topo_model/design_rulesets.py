"""Named design-ruleset registry — the bridge between a JSON document and the
callable :class:`~ada.topology.design_rules.DesignRules`.

A procedural document can't carry Python callables, so ``doc["design_rules"]``
names a ruleset by slug and the compiler resolves it here. Workers advertise
:func:`design_ruleset_specs` so the viewer can offer the choices in a dropdown.
"""

from __future__ import annotations

from ada.topology import DesignRules

from .penetration import standard_design_rules

__all__ = ["DESIGN_RULESETS", "resolve_design_rules", "design_ruleset_specs", "DEFAULT_DESIGN_RULESET"]

DEFAULT_DESIGN_RULESET = "standard"

# slug -> {name, description, factory}. ``factory`` builds a fresh DesignRules
# (rules carry no per-model state, but a fresh instance keeps them isolated).
DESIGN_RULESETS: dict[str, dict] = {
    "standard": {
        "name": "Standard details",
        "description": "Route runs and add a penetration detail at each wall crossing "
        "(pipe sleeve / cable transit block / duct frame) with a through-hole cut in the wall plate.",
        "factory": standard_design_rules,
    },
    "route_only": {
        "name": "Route only",
        "description": "Route runs and detect wall crossings, but emit no penetration detail geometry.",
        "factory": DesignRules,  # default: model_penetration=None
    },
}


def resolve_design_rules(name: str | None) -> DesignRules | None:
    """Build the ``DesignRules`` for a ruleset slug, or ``None`` for an unknown/
    empty name (callers fall back to their own default)."""
    spec = DESIGN_RULESETS.get((name or "").strip().lower())
    return spec["factory"]() if spec is not None else None


def design_ruleset_specs() -> list[dict]:
    """Advertisable catalog of the built-in rulesets (slug/name/description)."""
    return [
        {"slug": slug, "name": spec["name"], "description": spec["description"]}
        for slug, spec in DESIGN_RULESETS.items()
    ]
