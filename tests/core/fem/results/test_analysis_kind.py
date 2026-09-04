"""Which runs count as static, and why the answer cannot be a heuristic.

``_infer_analysis_kind`` reads a field's step values as a physical quantity —
eigen frequencies or times — and calls a run modal when they are positive and
ascending. A Sesam deck's step values are result-case NUMBERS, which are
positive and ascending by definition, so a ten-load-case deck came out as ten
mode shapes on its nodal fields while its element fields said static. The two
halves of one manifest disagreed.
"""

from ada.fem.results.artefacts import analysis_kind_from_result_cases


def test_a_combination_is_conclusive():
    # A recipe that superposes basic cases at factors. A modal analysis has no
    # such thing, so this settles it without guessing.
    cases = [
        {"n": 1, "name": "dead"},
        {"n": 2, "name": "live"},
        {"n": 3, "name": "uls", "combination": True, "makeup": "1.2·dead + 1.5·live"},
    ]
    assert analysis_kind_from_result_cases(cases) == "static"


def test_named_cases_alone_prove_nothing():
    # A SESTRA eigen run labels its modes too. Treating names as evidence would
    # force "static" on a mode shape and break the signed deformation sweep it
    # needs, so the heuristic must keep deciding here.
    cases = [{"n": 1, "name": "mode 1"}, {"n": 2, "name": "mode 2"}]
    assert analysis_kind_from_result_cases(cases) is None


def test_no_cases_leaves_the_heuristic_alone():
    assert analysis_kind_from_result_cases(None) is None
    assert analysis_kind_from_result_cases([]) is None


def test_a_falsy_combination_flag_is_not_a_combination():
    assert analysis_kind_from_result_cases([{"n": 1, "combination": False}]) is None


def test_tolerates_entries_that_are_not_dicts():
    # The reader owns this list; a malformed entry must not take the bake down.
    assert analysis_kind_from_result_cases([None, 7, "case", {"combination": True}]) == "static"
