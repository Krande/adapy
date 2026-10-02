"""A builder that DECLINES a joint (`NotApplicable`) hands it to the joint's next applicable spec from
the same provider, by priority -- the fall-through a provider's own design loop does.

The run this pins: a provider's highest-priority spec (a stub detail, needing boolean cuts) declined
every one of 279 plain joints, the detail job recorded each as failed, and the plain sibling spec
that would have built them all was never tried.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import ada
from ada.api.connections.spec import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    NotApplicable,
    _clear_registry,
    get_registered,
    register_connection,
)
from ada.clash.detail import build_detail


@pytest.fixture(autouse=True)
def isolate_registry():
    _clear_registry()
    yield
    _clear_registry()


def _spec(name: str, priority: int, capability: str | None) -> ConnectionSpec:
    return ConnectionSpec(
        name=name,
        roles=(
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX"}),
                angle_to_role=MemberRole.LANDING,
                angle_range=AngleRange(20.0, 165.0),
            ),
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"})),
        ),
        priority=priority,
        capability=capability,
    )


def _joint():
    landing = ada.Beam("landing", (0, 0, 0), (2, 0, 0), "BOX300x300x12x12")
    incoming = ada.Beam("incoming", (1, 0, 0), (1, 2, 0), "BOX300x300x12x12")
    return SimpleNamespace(members=[incoming, landing], landing=landing, centre=(1.0, 0.0, 0.0), contact=None)


def _built(label):
    def fn(*, landing, incoming, name=label, **_):
        part = ada.Part(name)
        part.add_beam(ada.Beam(f"{name}_stiffener", (0, 0, 0), (0, 0, 0.5), "IPE200"))
        return part

    return fn


def test_a_decline_falls_through_to_the_same_providers_next_spec():
    @register_connection(_spec("prov.stub", 20, "pool-x"))
    def stub(**_):
        raise NotApplicable("no boolean cuts on either member")

    register_connection(_spec("prov.plain", 10, "pool-x"))(_built("plain"))
    # Another provider's spec binds the joint too, at a priority in between -- and must NOT be the
    # fall-through: choosing another provider is a decision, not what "not mine" means.
    register_connection(_spec("other.any", 15, "pool-y"))(_built("other"))

    glb, stats = build_detail(
        get_registered("prov.stub"), spec_name="prov.stub", joint_ids=["j1"], by_id={"j1": _joint()}, gen_options={}
    )
    assert glb
    assert stats["built_by"] == {"prov.plain": 1}
    assert "skipped" not in stats


def test_a_providers_own_exception_named_NotApplicable_is_a_decline_too():
    class NotApplicable(Exception):  # a provider's, on a core that predates the shared one
        pass

    @register_connection(_spec("prov.stub", 20, "pool-x"))
    def stub(**_):
        raise NotApplicable("not mine")

    register_connection(_spec("prov.plain", 10, "pool-x"))(_built("plain"))
    _, stats = build_detail(
        get_registered("prov.stub"), spec_name="prov.stub", joint_ids=["j1"], by_id={"j1": _joint()}, gen_options={}
    )
    assert stats["built_by"] == {"prov.plain": 1}


def test_a_crash_is_not_a_decline_and_is_not_passed_on():
    @register_connection(_spec("prov.stub", 20, "pool-x"))
    def stub(**_):
        raise ValueError("builder bug")

    register_connection(_spec("prov.plain", 10, "pool-x"))(_built("plain"))
    with pytest.raises(ValueError, match="builder bug"):
        build_detail(
            get_registered("prov.stub"), spec_name="prov.stub", joint_ids=["j1"], by_id={"j1": _joint()}, gen_options={}
        )


def test_when_every_candidate_declines_the_joint_names_each_reason():
    @register_connection(_spec("prov.stub", 20, "pool-x"))
    def stub(**_):
        raise NotApplicable("needs cuts")

    @register_connection(_spec("prov.plain", 10, "pool-x"))
    def plain(**_):
        raise NotApplicable("needs a gap")

    with pytest.raises(ValueError) as err:
        build_detail(
            get_registered("prov.stub"), spec_name="prov.stub", joint_ids=["j1"], by_id={"j1": _joint()}, gen_options={}
        )
    assert "prov.stub: needs cuts" in str(err.value) and "prov.plain: needs a gap" in str(err.value)
