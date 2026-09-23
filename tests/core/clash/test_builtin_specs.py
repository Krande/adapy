"""The built-in detailing engine's declared specs -- registration, and what they may bind to.

The central property this file pins is the one that motivated dropping the base-plate
declaration (``builtin_specs.py``'s own note): a spec must not bind to a joint merely because it
contains a beam. Measured on the demo before the base-plate declaration was removed: 84 joints out
of 84 -- i.e. useless. ``builtin.girder_gusset`` must not repeat that mistake.
"""

from __future__ import annotations

import pytest

import ada
from ada.api.connections.spec import (
    MemberRole,
    _clear_registry,
    all_registered,
    get_registered,
)
from ada.clash import ClashOptions, run_clash_check
from ada.clash.builtin_specs import (
    BOX_JOINT_SPEC,
    BUILTIN_SPEC_NAMES,
    GIRDER_GUSSET_SPEC,
    register_builtin_specs,
)
from ada.clash.match import bindings_for_spec
from ada.topo_model import build_topo_model


@pytest.fixture(autouse=True)
def clean_registry():
    """The registry is process-global; every test starts and ends with it cleared."""
    _clear_registry()
    yield
    _clear_registry()


# ── registration is tolerant, not idempotent-by-flag ─────────────────


def test_register_builtin_specs_is_tolerant_of_being_called_twice():
    register_builtin_specs()
    register_builtin_specs()  # must not raise "already registered"
    names = {r.spec.name for r in all_registered()}
    assert names == set(BUILTIN_SPEC_NAMES)


def test_register_builtin_specs_recovers_after_clear_registry():
    # A module-level "already ran" flag would leave a cleared registry permanently empty --
    # builtin_specs.py's own docstring names this as the reason it checks `_REGISTRY` membership
    # instead. Tests clear the registry between them, so this must actually work, not just be
    # claimed.
    register_builtin_specs()
    _clear_registry()
    assert all_registered() == []
    register_builtin_specs()
    assert {r.spec.name for r in all_registered()} == set(BUILTIN_SPEC_NAMES)


# ── the registered builders are the built-in joint classes ──────────


def test_girder_gusset_builder_produces_a_connection_for_a_real_binding():
    # Real I-family Girder beams from the SteelStru demo, matched through the real matcher, built
    # by the real registered builder -- not a mock anywhere in the chain.
    register_builtin_specs()
    model = build_topo_model()
    girders = [
        b
        for b in model.get_all_physical_objects(by_type=ada.Beam)
        if b.member_type == "Girder" and b.section.type.value.upper() == "I"
    ]
    assert len(girders) >= 2

    bindings = None
    for i, g1 in enumerate(girders):
        for g2 in girders[i + 1 :]:
            found = bindings_for_spec(GIRDER_GUSSET_SPEC, [g1, g2])
            if found:
                bindings = found
                break
        if bindings:
            break
    assert bindings, "expected at least one real girder pair the spec's roles can bind"

    binding = bindings[0]
    landing, incoming = binding[MemberRole.LANDING], binding[MemberRole.INCOMING]
    reg = get_registered(GIRDER_GUSSET_SPEC.name)
    connection = reg.fn(landing=landing, incoming=incoming, centre=(0.0, 0.0, 0.0), name="probe")

    from ada.api.connections.joints import Connection

    assert isinstance(connection, Connection)
    assert list(connection.get_all_physical_objects(by_type=ada.Plate))


def test_box_joint_is_registered_with_its_builder():
    register_builtin_specs()
    reg = get_registered(BOX_JOINT_SPEC.name)
    assert reg.spec is BOX_JOINT_SPEC
    assert callable(reg.fn)


# ── the property that motivated dropping the base-plate declaration ─


def test_girder_gusset_does_not_bind_to_every_joint_that_merely_contains_a_beam():
    # Pinned: 12 of the demo's 84 joints, not 84 -- the failure mode the base-plate declaration
    # was removed for (it matched every one of the 84).
    register_builtin_specs()
    model = build_topo_model()
    result = run_clash_check(model, source_key="demo", options=ClashOptions(include_plate_joints=False))

    assert len(result.joints) == 84
    matches = [j for j in result.joints if any(a.spec == "builtin.girder_gusset" for a in j.applicable)]
    assert len(matches) == 12
    assert len(matches) < len(result.joints)


def test_no_built_in_spec_matches_the_hp_only_groups():
    # The two HP-mixed groups (48 + 24 joints) must offer nothing: HP is excluded from the
    # girder family on purpose (builtin_specs.py's own note -- HP bulb-flat stringers are
    # secondary members the detailing engine does not gusset).
    register_builtin_specs()
    model = build_topo_model()
    result = run_clash_check(model, source_key="demo", options=ClashOptions(include_plate_joints=False))

    hp_groups = [g for g in result.groups if "HP" in g.type_label]
    assert hp_groups, "expected the HP-mixed groups to be present on the pinned demo"
    for group in hp_groups:
        assert group.applicable == ()
