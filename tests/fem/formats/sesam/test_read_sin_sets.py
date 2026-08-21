"""Named sets and model description from a Sesam SIN.

Driven by a fake ``SinFile`` rather than a committed binary. The logic under test is
record assembly -- accumulating members across records, splitting a set that holds both
kinds, joining names from a second card -- and every one of those properties is expressible
in a handful of literal records. A real SIN would make the test slower, opaque about which
property failed, and would have to be several megabytes in the repo.
"""

from __future__ import annotations

import pytest

from ada.fem.formats.sesam.results.read_sin_sets import read_sin_groups, read_sin_model_info


class FakeSin:
    """Just enough of ``SinFile`` for the set reader.

    Deliberately mimics the three doors ``_records_for`` actually uses --
    ``type_blocks``, ``iter_records``, ``iter_text_records`` -- and returns records in the
    RAW shape the real reader yields, without the NFIELD prefix. That prefix is added by
    ``_records_for`` itself, so producing it here would have skipped the very field-offset
    arithmetic the set reader depends on and tested the mock instead.
    """

    def __init__(
        self,
        numeric: dict[str, list[tuple]] | None = None,
        text: dict[str, list[tuple[tuple, str]]] | None = None,
        counts: dict[str, int] | None = None,
    ):
        self._numeric = numeric or {}
        self._text = text or {}
        self._counts = counts or {}
        self.type_blocks = {**self._numeric, **self._text}

    def iter_records(self, name: str, where_first_word=None, where_second_word=None):
        for rec in self._numeric.get(name, []):
            if where_first_word is not None and rec[0] != where_first_word:
                continue
            if where_second_word is not None and rec[1] not in where_second_word:
                continue
            yield rec

    def iter_text_records(self, name: str):
        yield from self._text.get(name, [])

    def get_count(self, name: str) -> int:
        if name not in self._counts:
            raise KeyError(name)
        return self._counts[name]


# GSETMEMB raw record: (isref, index, istype, isorig, *members).
# istype 1 = nodes, 2 = elements.
def memb(isref: int, istype: int, *members: int) -> tuple:
    return (isref, 1, istype, 0, *members)


# TDSETNAM raw text record: ((isref, codnam, codtxt), name).
def name(isref: int, text: str) -> tuple:
    return ((isref, 0, 0), text)


def sin_with(memb_records=(), name_records=(), counts=None) -> FakeSin:
    return FakeSin(
        numeric={"GSETMEMB": list(memb_records)} if memb_records else {},
        text={"TDSETNAM": list(name_records)} if name_records else {},
        counts=counts,
    )


def test_members_accumulate_across_records():
    """A set is emitted in chunks; taking the first record gives a truncated set.

    The worst kind of wrong, because a short set looks entirely plausible.
    """
    sin = sin_with([memb(1, 2, 10, 11), memb(1, 2, 12), memb(1, 2, 13, 14)], [name(1, "deck")])
    groups = read_sin_groups(sin)
    assert len(groups) == 1
    assert groups[0]["members"] == ["E10", "E11", "E12", "E13", "E14"]


def test_one_set_id_holding_both_kinds_splits_and_is_suffixed():
    """Nodes and elements arrive under a single isref. The manifest's group shape is
    single-kind, and two identically named rows would be indistinguishable in the UI."""
    sin = sin_with([memb(7, 1, 1, 2, 3), memb(7, 2, 100, 101)], [name(7, "Mini")])
    groups = read_sin_groups(sin)
    by_name = {g["name"]: g for g in groups}
    assert set(by_name) == {"Mini (nodes)", "Mini (elements)"}
    assert by_name["Mini (nodes)"]["fe_object_type"] == "node"
    assert by_name["Mini (nodes)"]["members"] == ["P1", "P2", "P3"]
    assert by_name["Mini (elements)"]["members"] == ["E100", "E101"]


def test_single_kind_sets_are_not_suffixed():
    """Suffixing every set would put "(elements)" on the majority of names for no reason."""
    sin = sin_with([memb(1, 2, 5)], [name(1, "supports")])
    assert [g["name"] for g in read_sin_groups(sin)] == ["supports"]


def test_element_members_use_the_viewer_s_range_prefix():
    """``E{id}`` is what the streaming loader builds its draw ranges from.

    A member with any other prefix matches no range: the set selects nothing, silently,
    while still reporting a member count. This pinned the bug that shipped once already.
    """
    sin = sin_with([memb(1, 2, 42)], [name(1, "x")])
    assert read_sin_groups(sin)[0]["members"] == ["E42"]


def test_repeated_members_are_counted_once():
    sin = sin_with([memb(1, 2, 8, 9), memb(1, 2, 9, 10)], [name(1, "x")])
    assert read_sin_groups(sin)[0]["members"] == ["E8", "E9", "E10"]


def test_a_set_with_no_name_still_appears():
    """A nameless set is still a set. Dropping it would lose members with no trace."""
    sin = sin_with([memb(3, 2, 1)])
    assert read_sin_groups(sin)[0]["name"] == "Set 3"


def test_no_sets_returns_none():
    """What the bake expects from a reader with nothing to contribute."""
    assert read_sin_groups(sin_with()) is None
    assert read_sin_groups(sin_with([])) is None


def test_model_info_reports_totals():
    sin = sin_with(counts={"GNODE": 1057, "GELMNT1": 2461, "HIERARCH": 1})
    info = read_sin_model_info(sin)
    assert info["n_nodes"] == 1057
    assert info["n_elements"] == 2461


def test_one_super_element_gets_the_model_totals():
    """With exactly one SE the totals are provably its counts."""
    sin = sin_with(counts={"GNODE": 10, "GELMNT1": 20, "HIERARCH": 1})
    (se,) = read_sin_model_info(sin)["super_elements"]
    assert (se["n_nodes"], se["n_elements"]) == (10, 20)


def test_several_super_elements_report_no_counts_rather_than_a_guess():
    """Splitting totals across SEs needs an element-to-SE association this file does not
    carry. A wrong number is worse than a missing one in a panel people size work from."""
    sin = sin_with(counts={"GNODE": 10, "GELMNT1": 20, "HIERARCH": 3})
    ses = read_sin_model_info(sin)["super_elements"]
    assert len(ses) == 3
    assert all(se["n_nodes"] is None and se["n_elements"] is None for se in ses)


@pytest.mark.parametrize("counts", [{}, {"GNODE": 0, "GELMNT1": 0}])
def test_a_file_we_cannot_describe_returns_none(counts):
    assert read_sin_model_info(sin_with(counts=counts)) is None
