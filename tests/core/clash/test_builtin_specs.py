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


# ── the connection builder builds them too ───────────────────────────


@pytest.mark.parametrize("spec_name", BUILTIN_SPEC_NAMES)
def test_a_built_in_spec_builds_in_the_connection_builder_without_a_clash_point(spec_name, tmp_path):
    """The connection builder passes no ``centre``: the joint is where the sample members meet.
    Without the default both builders raised TypeError there."""
    from ada.api.connections import build_component
    from ada.api.connections.library import build_subject
    from ada.api.connections.sample import default_inputs

    register_builtin_specs()
    inputs = default_inputs(get_registered(spec_name).spec)
    assert build_component(spec_name, inputs) is not None
    if spec_name == GIRDER_GUSSET_SPEC.name:
        # Its welds are straight extrusions with no sweep curve: the subject reads their two points.
        assert build_subject(spec_name, inputs, tmp_path / "subject.json")["welds"] == 4
    else:
        # The box joint welds nothing, so as a simulation subject it is "no welds", not a crash.
        with pytest.raises(ValueError, match="no welds"):
            build_subject(spec_name, inputs, tmp_path / "subject.json")


# ── what the connection builder makes of them is weldable ────────────
#
# The joint point the builder passes is where the sample members' axes cross: on the landing's
# centreline. Placed from that point, the gusset sat in the landing's web and both beads ran along the
# centrelines, 150 mm inside the steel (half an IPE300's depth), where no torch reaches them.

_LENGTHS = {"default": None, "short": {"landing": 0.8, "incoming": 0.4}}


def _mesh(obj):
    from ada.simulation.subjects import _member_mesh

    vertices, faces = _member_mesh(obj)
    return vertices[faces]


def _winding(point, tri) -> float:
    """The generalised winding number of a closed mesh about ``point``: 1 inside, 0 outside."""
    import numpy as np

    a, b, c = tri[:, 0] - point, tri[:, 1] - point, tri[:, 2] - point
    la, lb, lc = (np.linalg.norm(v, axis=1) for v in (a, b, c))
    num = np.einsum("ij,ij->i", a, np.cross(b, c))
    den = (
        la * lb * lc
        + np.einsum("ij,ij->i", a, b) * lc
        + np.einsum("ij,ij->i", b, c) * la
        + np.einsum("ij,ij->i", c, a) * lb
    )
    return float(2 * np.arctan2(num, den).sum() / (4 * np.pi))


def _built(spec_name, lengths):
    from ada.api.connections import build_component
    from ada.api.connections.sample import default_inputs

    register_builtin_specs()
    inputs = default_inputs(get_registered(spec_name).spec)
    for role, length in (lengths or {}).items():
        inputs[role]["length"] = length
    return build_component(spec_name, inputs)


@pytest.mark.parametrize("lengths", list(_LENGTHS.values()), ids=list(_LENGTHS))
@pytest.mark.parametrize("spec_name", BUILTIN_SPEC_NAMES)
def test_every_weld_of_a_built_in_spec_lies_on_the_members_it_joins(spec_name, lengths):
    import numpy as np

    from ada.simulation.subjects import _distance_to_triangles, _weld_line

    conn = _built(spec_name, lengths)
    solids = {m.name: _mesh(m) for m in conn.get_all_physical_objects() if not isinstance(m, ada.Weld)}
    welds = list(conn.get_all_welds())
    if spec_name == BOX_JOINT_SPEC.name:
        assert welds == []  # the box joint is the cut alone (see the next test)
        return
    assert len(welds) == 4
    for weld in welds:
        points, _ = _weld_line(weld)
        line = np.linspace(points[0], points[-1], 21)
        assert len(weld.members) == 2
        for member in weld.members:
            tri = solids[member.name]
            gap = max(float(_distance_to_triangles(p, tri)[0].min()) for p in line)
            assert gap < 1e-6, f"{weld.name} is {gap * 1e3:.1f} mm off {member.name}"
        # Its bead is in the corner between them, not inside any solid of the joint.
        bead = _mesh(weld.geometry).reshape(-1, 3).mean(axis=0)
        for name, tri in solids.items():
            assert abs(_winding(bead, tri)) < 0.01, f"{weld.name}'s bead is inside {name}"


@pytest.mark.parametrize("lengths", list(_LENGTHS.values()), ids=list(_LENGTHS))
@pytest.mark.parametrize("spec_name", BUILTIN_SPEC_NAMES)
def test_the_incoming_of_a_built_in_spec_butts_the_landing_rather_than_running_into_it(spec_name, lengths):
    """The sample's incoming starts on the landing's centreline; the joint cuts it back to the
    landing's face (the box joint, by its clearance) or to the end plate standing on it."""
    conn = _built(spec_name, lengths)
    beams = {b.name: b for b in conn.get_all_physical_objects(by_type=ada.Beam)}
    landing, incoming = _mesh(beams["sample_landing"]), _mesh(beams["sample_incoming"])
    face = float(landing[..., 1].max())  # the incoming runs along +Y
    start = float(incoming[..., 1].min())
    assert start > face
    plates = list(conn.get_all_physical_objects(by_type=ada.Plate))
    if spec_name == BOX_JOINT_SPEC.name:
        assert plates == []
        assert start == pytest.approx(face + 2e-3, abs=1e-6)
        return
    (plate,) = plates
    plate_tri = _mesh(plate)
    # The plate stands on the landing's flange tips, out of its web, and the incoming butts it.
    assert float(plate_tri[..., 1].min()) == pytest.approx(face, abs=1e-6)
    assert start == pytest.approx(float(plate_tri[..., 1].max()), abs=1e-6)
    if lengths:
        assert float(incoming[..., 1].max()) == pytest.approx(lengths["incoming"], abs=1e-6)


def test_a_built_in_specs_weld_assembly_keeps_welds_and_members_in_one_frame():
    """The planner reads the welds and the members from the subject, in its part frame: each weld
    must still be on the meshes of the members it names there."""
    import numpy as np

    from ada.simulation.subjects import _distance_to_triangles, weld_assembly_from_part

    for lengths in _LENGTHS.values():
        conn = _built(GIRDER_GUSSET_SPEC.name, lengths)
        doc = weld_assembly_from_part(conn)
        members = {m["name"]: np.asarray(m["vertices"])[np.asarray(m["faces"])] for m in doc["members"]}
        assert len(doc["welds"]) == 4
        for weld in doc["welds"]:
            points = np.asarray(weld["points"])
            for name in weld["members"]:
                gap = max(float(_distance_to_triangles(p, members[name])[0].min()) for p in points)
                assert gap < 1e-5, f"{weld['name']} is {gap * 1e3:.1f} mm off {name} in the part frame"


def test_a_through_crossing_keeps_the_marker_gusset():
    # Neither girder ends at the other: there is nothing to seat an end plate against, and the
    # members are left whole.
    from ada.topo_model.detail_joints import GirderJoint

    g1 = ada.Beam("g1", (0, 2.5, 0), (5, 2.5, 0), "IPE200")
    g2 = ada.Beam("g2", (2.5, 0, 0), (2.5, 5, 0), "IPE200")
    joint = GirderJoint("x", [g1, g2], (2.5, 2.5, 0.0))
    assert len(joint.connection.welds) == 2
    assert not g1.booleans and not g2.booleans
