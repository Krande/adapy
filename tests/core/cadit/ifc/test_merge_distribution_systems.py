"""Folding several IfcDistributionSystems into one keeps every member."""

from __future__ import annotations

from types import SimpleNamespace

import ifcopenshell

from ada.cadit.ifc.write.write_equipment import _merge_distribution_systems
from ada.core.guid import create_guid


def _file_with_systems(primary_members: list | None):
    f = ifcopenshell.file(schema="IFC4")
    primary = f.create_entity("IfcDistributionSystem", create_guid(), None, "branch-TE-1")
    extra = f.create_entity("IfcDistributionSystem", create_guid(), None, "L-1/2_route")
    seg_a = f.create_entity("IfcPipeSegment", create_guid(), None, "seg-a")
    seg_b = f.create_entity("IfcPipeSegment", create_guid(), None, "seg-b")
    if primary_members is not None:
        members = [seg_a] if primary_members == ["a"] else []
        f.create_entity(
            "IfcRelAssignsToGroup", create_guid(), None, None, None, RelatedObjects=members, RelatingGroup=primary
        )
    f.create_entity(
        "IfcRelAssignsToGroup", create_guid(), None, None, None, RelatedObjects=[seg_a, seg_b], RelatingGroup=extra
    )
    return f, primary, extra, seg_a, seg_b


def _members(f, group):
    return [o for r in f.by_type("IfcRelAssignsToGroup") if r.RelatingGroup == group for o in r.RelatedObjects]


def test_a_primary_without_a_membership_relationship_gets_one_and_the_members():
    f, primary, extra, seg_a, seg_b = _file_with_systems(primary_members=None)
    store = SimpleNamespace(f=f, owner_history=None)

    _merge_distribution_systems(store, primary, [extra])

    # The extra's members used to be dropped on the floor here: its relationship was removed and,
    # with no primary relationship to move them onto, nothing was re-homed.
    rels = [r for r in f.by_type("IfcRelAssignsToGroup") if r.RelatingGroup == primary]
    assert len(rels) == 1
    assert set(rels[0].RelatedObjects) == {seg_a, seg_b}
    assert rels[0].Name == "branch-TE-1"
    assert not f.by_type("IfcDistributionSystem")[1:] or extra not in f.by_type("IfcDistributionSystem")
    assert not [r for r in f.by_type("IfcRelAssignsToGroup") if r.RelatingGroup is None]


def test_an_existing_primary_relationship_is_extended_without_duplicates():
    f, primary, extra, seg_a, seg_b = _file_with_systems(primary_members=["a"])
    store = SimpleNamespace(f=f, owner_history=None)

    _merge_distribution_systems(store, primary, [extra])

    rels = [r for r in f.by_type("IfcRelAssignsToGroup") if r.RelatingGroup == primary]
    assert len(rels) == 1
    assert list(rels[0].RelatedObjects) == [seg_a, seg_b]
    assert len(f.by_type("IfcDistributionSystem")) == 1
