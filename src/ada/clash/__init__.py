"""Joint identification, typing and grouping -- core's half of the Clashes surface.

Core IDENTIFIES and TYPES; a generator DETAILS. The split is Decision 10's: the primitives that
find and classify joints are core's already (``Connections.find``, ``MemberCriteria``, the spec
registry), while the mesh clash with penetration depth and contact patches is a generator's need
and is recomputed on the generator's own pool from the real members.

Nothing in this package may name a fabrication process, a vendor system or a package that
registers specs -- see ``classify`` for the closed list of terms a joint type may be built from.

IMPORTING THIS PACKAGE IS CHEAP, ON PURPOSE. The names below resolve on first use
(:pep:`562` module ``__getattr__``), because the two halves of this package have very different
weights: ``result`` and ``options`` are stdlib-only documents that the SLIM api reads and echoes,
while ``identify``/``classify``/``match`` import ``ada.api`` -- beams, plates, containers, numpy --
and only ever run where a model is actually opened, which is a worker. An eager re-export here
would drag the modelling stack into an image built not to have it, and that does not fail at build
time: it crashloops the API on start.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # import-time names for type checkers only; resolved at runtime by __getattr__
    from ada.clash.classify import (  # noqa: F401
        angle_bucket,
        describe_member,
        type_key_for,
        type_label_for,
    )
    from ada.clash.from_asset import (  # noqa: F401
        clash_check_from_asset_node,
        part_for_asset_node,
    )
    from ada.clash.from_scan import (  # noqa: F401
        clash_check_from_members,
        clash_check_from_scan,
    )
    from ada.clash.identify import identify_joints, run_clash_check  # noqa: F401
    from ada.clash.match import (  # noqa: F401
        applicable_specs,
        bindings_for_spec,
        detail_pairs,
    )
    from ada.clash.result import (  # noqa: F401
        ApplicableSpec,
        ClashResult,
        ClashResultError,
        JointGroup,
        JointMember,
        JointRecord,
        group_joints,
        parse_clash_result,
    )

from ada.clash.options import ClashOptions  # stdlib-only, and read by the slim api
from ada.clash.result import (
    CLASH_RESULT_SCHEMA,  # ditto: a constant, no imports of its own
)

#: name -> the submodule that defines it. Kept explicit rather than scanned, so what this package
#: exports is readable here and a typo is an AttributeError at import of the NAME, not a silent miss.
_LAZY = {
    "ApplicableSpec": "result",
    "ClashResult": "result",
    "ClashResultError": "result",
    "JointGroup": "result",
    "JointMember": "result",
    "JointRecord": "result",
    "group_joints": "result",
    "parse_clash_result": "result",
    "angle_bucket": "classify",
    "describe_member": "classify",
    "type_key_for": "classify",
    "type_label_for": "classify",
    "applicable_specs": "match",
    "bindings_for_spec": "match",
    "detail_pairs": "match",
    "identify_joints": "identify",
    "run_clash_check": "identify",
    "clash_check_from_members": "from_scan",
    "clash_check_from_scan": "from_scan",
    "clash_check_from_asset_node": "from_asset",
    "part_for_asset_node": "from_asset",
}

__all__ = ["CLASH_RESULT_SCHEMA", "ClashOptions", *sorted(_LAZY)]


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(f"ada.clash.{module}"), name)


def __dir__() -> list[str]:
    return sorted(__all__)
