"""The names of the built-in joint declarations -- strings, and nothing else.

Separate from ``builtin_specs`` for the reason ``options`` is separate from ``identify``: the REST
route needs to know whether a spec name is one of core's own (a built-in runs wherever core runs,
so it needs no capability), and it is served by the SLIM api, which has no modelling stack.
``builtin_specs`` imports ``ada.api.connections`` to DECLARE the specs; this module declares only
what they are called.
"""

from __future__ import annotations

GIRDER_GUSSET_SPEC_NAME = "builtin.girder_gusset"
BOX_JOINT_SPEC_NAME = "builtin.box_joint"

#: Every spec core itself registers. A name in here is served by the default pool.
BUILTIN_SPEC_NAMES = (GIRDER_GUSSET_SPEC_NAME, BOX_JOINT_SPEC_NAME)

#: Core's own clash checker (``ada.clash.passes.ClashChecker``). Here for the same reason: the route
#: decides whether a check needs routing to a pool before anything that registers checkers loads.
BUILTIN_CHECKER = "adapy"

#: Core's convex-contact checker and its one pass (``ada.clash.convex_contact``): convex-hull
#: interference measured by adacpp, with a contact normal, penetration depth and contact patch.
#: Wire constants -- the pass name is every joint's ``origin`` and part of its id.
CONVEX_CONTACT_CHECKER = "convex-contact"
CONVEX_CONTACT_PASS = "convex-contact"

#: Every checker core itself registers. They run wherever core runs, so they are never routed to a
#: pool: the api decides that from this list, before anything that registers checkers loads.
CORE_CHECKERS = (BUILTIN_CHECKER, CONVEX_CONTACT_CHECKER)

__all__ = [
    "BOX_JOINT_SPEC_NAME",
    "BUILTIN_CHECKER",
    "BUILTIN_SPEC_NAMES",
    "CONVEX_CONTACT_CHECKER",
    "CONVEX_CONTACT_PASS",
    "CORE_CHECKERS",
    "GIRDER_GUSSET_SPEC_NAME",
]
