"""A Sesam set with members and no name must not kill the mesh conversion.

``GSETMEMB`` lists a set's members and ``TDSETNAM`` names it, and a production
deck can carry the first without the second. ``SifReader.get_sets`` looked the
name up with ``set_map[set_id]``, so such a deck raised ``KeyError: <id>`` from
the middle of ``get_sif_mesh`` and the whole FEA bake failed with nothing
pointing at the cause.

``results/sets.py``'s ``manifest_groups`` already skipped those records for the
viewer manifest. This pins the same behaviour on the reader the mesh path uses.
"""

from __future__ import annotations

from ada.fem.formats.sesam.results.read_sif import SifReader


class _Reader(SifReader):
    """A SifReader with the two record sources ``get_sets`` reads stubbed.

    ``get_sets`` is the unit under test and its only inputs are these two
    methods, so driving them directly is both the smallest test and the one that
    keeps working when the SIF card parsing changes underneath.
    """

    def __init__(self, members, names):
        self._members = members
        self._names = names

    def get_gsetmemb(self):
        return self._members

    def get_tdsetnam_map(self):
        return self._names


def _members(**by_id):
    return {
        set_id: {"elset": spec.get("elset", []), "nset": spec.get("nset", [])}
        for set_id, spec in ((int(k.lstrip("s")), v) for k, v in by_id.items())
    }


def test_a_set_with_members_and_no_name_is_skipped_not_fatal() -> None:
    reader = _Reader(
        members=_members(s1={"elset": [101, 102, 103]}, s2={"elset": [201, 202]}),
        names={2: (2, "named_elset")},  # set 1 deliberately absent
    )

    sets = reader.get_sets()

    # Keyed by name, so the check is on the keys: the named set survives and the
    # unnamed one is absent rather than present under some invented label.
    # (`FemSet.members` resolves lazily against a parent FEM, which a detached
    # set has not got, so the membership is not touched here.)
    assert sorted(sets) == ["named_elset"], "the unnamed set should be dropped, not named"
    assert sets["named_elset"].type == "elset"


def test_the_named_sets_still_come_through_unchanged() -> None:
    reader = _Reader(
        members=_members(s1={"elset": [1, 2]}, s2={"nset": [10, 11]}),
        names={1: (1, "elements"), 2: (2, "nodes")},
    )

    sets = reader.get_sets()

    assert sorted(sets) == ["elements", "nodes"]
    # A set carrying only node records is an nset; element membership wins when
    # a set has both, which is what element scoping needs.
    assert sets["elements"].type == "elset"
    assert sets["nodes"].type == "nset"


def test_every_set_being_unnamed_yields_an_empty_mapping() -> None:
    # Empty, not None: None means "this deck records no sets at all", which is a
    # different thing for the caller to reason about.
    reader = _Reader(members=_members(s1={"elset": [1]}, s2={"elset": [2]}), names={})

    assert reader.get_sets() == {}


def test_no_member_records_still_reports_none() -> None:
    reader = _Reader(members=None, names={1: (1, "unused")})

    assert reader.get_sets() is None
