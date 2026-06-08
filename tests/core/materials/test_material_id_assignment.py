"""Regression: every material in a Materials collection must end up with a unique integer id.

Materials default to id=None. Building a collection from id-less materials used to collapse them
onto a single ``{None: material}`` id-map entry (silently losing members) and then crash id
allocation (max_id / add) on ``None + 1``. recreate_name_and_id_maps now assigns a fresh unique
id to any member with a missing / non-integer / duplicate id, so the id-map is faithful and FEM
exports (which reference materials by id) stay correct — rather than merely hiding the id-less
members from the map.
"""

from ada import Material
from ada.api.containers.materials import Materials


def test_idless_materials_get_unique_ids_on_construct():
    m1, m2, m3 = Material("A"), Material("B"), Material("C")
    assert m1.id is None  # default

    mats = Materials([m1, m2, m3])

    ids = [m.id for m in mats]
    assert all(isinstance(i, int) for i in ids)
    assert len(set(ids)) == 3  # no collapse onto a single None key
    assert len(mats.id_map) == 3
    assert mats.max_id == max(ids)


def test_max_id_empty_is_zero():
    assert Materials([]).max_id == 0


def test_add_assigns_and_resolves_collisions():
    mats = Materials([Material("A"), Material("B")])  # -> ids 1, 2

    fresh = mats.add(Material("C"))  # None id
    assert fresh.id == mats.max_id
    assert isinstance(fresh.id, int)

    clash = Material("D")
    clash.id = 1  # collides with existing
    mats.add(clash)
    assert clash.id != 1

    ids = [m.id for m in mats]
    assert len(set(ids)) == len(ids)  # still all unique


def test_every_material_addressable_by_id():
    # The point of assigning ids (vs dropping id-less members): get_by_id must find them.
    mats = Materials([Material("A"), Material("B")])
    for m in mats:
        assert mats.get_by_id(m.id) is m
