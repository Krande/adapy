"""Is this worker fit to serve this capability?

The guard today is `ADA_WORKER_BASE_CONVERSIONS=false`, set by hand on each
foreign pool — a correctness property defended by remembering an env var. This
inverts it. Everything under test is a pure function, because what counts as
fit is the part that has to be exactly right and it should not need a cluster
to exercise.

The comparator is deliberately narrow and refuses rather than guesses; a good
half of these tests are about the refusing.
"""

import pytest

from ada.comms.rest.qualification import (
    Requirement,
    compare_versions,
    evaluate,
    parse_requirements,
    satisfies,
)


def pkg(name, version, build=None, channel="conda-forge"):
    return {"name": name, "version": version, "build": build, "channel": channel}


# --- ordering --------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("1.0.0", "1.0.0", 0),
        ("0.51.0", "0.51.0", 0),
        ("0.52.0", "0.51.0", 1),
        ("0.51.0", "0.52.0", -1),
        ("7.8.1", "7.8.0", 1),
        ("1.0.119.0", "1.0.9.0", 1),  # numeric, not lexical: 119 > 9
        ("2024.1", "2023.12", 1),
        ("1.2", "1.2.0", 0),  # zero-padded: what ">=1.2" means to whoever wrote it
        ("1.2.1", "1.2", 1),
        ("10.0", "9.0", 1),
    ],
)
def test_dotted_numeric_releases_order_numerically(a, b, expected):
    assert compare_versions(a, b) == expected


def test_a_leading_v_is_tolerated():
    assert compare_versions("v1.2.0", "1.2.0") == 0


@pytest.mark.parametrize(
    "a,b",
    [
        ("1.0.0rc1", "1.0.0"),
        ("1.0.0", "1.0.0b2"),
        ("1!2.0", "2.0"),  # conda epoch
        ("1.0.0+local", "1.0.0"),
        ("2024a", "2024b"),
        ("", "1.0"),
    ],
)
def test_anything_else_is_refused_rather_than_guessed(a, b):
    """The point of the comparator being narrow.

    Conda versions are not PEP 440; epochs, locals and pre-release suffixes all
    order differently between ecosystems. Quietly picking an interpretation of
    `1.0.0rc1` vs `1.0.0` is worse than saying it cannot tell — one of the two
    answers silently disables a healthy pool, the other silently keeps a stale
    one running.
    """
    assert compare_versions(a, b) is None


def test_identical_unorderable_strings_still_compare_equal():
    # Equality needs no ordering, so an exact pin works even where `>=` cannot.
    assert compare_versions("1.0.0rc1", "1.0.0rc1") == 0


# --- specs -----------------------------------------------------------------


@pytest.mark.parametrize(
    "installed,spec,ok",
    [
        ("0.51.0", ">=0.51.0", True),
        ("0.52.0", ">=0.51.0", True),
        ("0.44.1", ">=0.51.0", False),
        ("1.5.0", ">=1.0,<2.0", True),
        ("2.0.0", ">=1.0,<2.0", False),
        ("1.2.3", "==1.2.3", True),
        ("1.2.4", "==1.2.3", False),
        ("1.2.3", "1.2.3", True),  # bare version means ==
        ("1.2.3", "!=1.2.3", False),
        ("1.2.4", "!=1.2.3", True),
        ("1.2.3", ">1.2.3", False),
        ("1.2.4", ">1.2.3", True),
    ],
)
def test_specs_are_evaluated_clause_by_clause(installed, spec, ok):
    assert satisfies(installed, spec)[0] is ok


def test_every_clause_must_hold():
    assert satisfies("0.9.0", ">=1.0,<2.0")[0] is False


def test_an_unorderable_comparison_fails_and_names_both_versions():
    ok, why = satisfies("1.0.0rc1", ">=1.0.0")
    assert ok is False
    assert "1.0.0rc1" in why and "1.0.0" in why
    assert "cannot order" in why


def test_an_exact_pin_works_on_an_unorderable_version():
    # The escape hatch for a genuinely ambiguous version: pin it exactly.
    assert satisfies("1.0.0rc1", "==1.0.0rc1")[0] is True


def test_an_empty_spec_is_satisfied():
    # Nothing asked for, nothing to fail.
    assert satisfies("1.0.0", "")[0] is True


# --- the document ----------------------------------------------------------


def test_a_requirement_document_is_read_case_insensitively():
    parsed = parse_requirements({"Base": {"requires": {"Ada-Py": ">=0.51.0"}}})
    assert parsed["base"].requires == {"ada-py": ">=0.51.0"}


@pytest.mark.parametrize("doc", [None, [], "nope", 42, {"base": "not-a-dict"}, {"": {}}])
def test_a_malformed_document_yields_no_entries_rather_than_raising(doc):
    # A typo in an admin-authored document must not become a worker that serves
    # nothing. Unreadable entries are dropped, which leaves their capability
    # ungated exactly as if none had been written.
    assert parse_requirements(doc) == {} or all(isinstance(v, Requirement) for v in parse_requirements(doc).values())


# --- the verdict -----------------------------------------------------------

REQS = {
    "base": {"requires": {"ada-py": ">=0.51.0", "occt": ">=7.8.1"}, "build_match": {"occt": "novtk_*"}},
    "meshing": {"requires": {"example-mesher": ">=1.4.0"}},
}
GOOD = [pkg("ada-py", "0.52.0"), pkg("occt", "7.8.1", build="novtk_h1234_0"), pkg("example-mesher", "1.4.0")]


def test_a_fit_environment_keeps_everything():
    v = evaluate(["base", "meshing"], REQS, GOOD)
    assert v.kept == ["base", "meshing"]
    assert v.withheld == []


def test_a_capability_with_no_entry_is_kept():
    """Fail open on silence.

    Backwards compatible, and a plugin capability that governs its own fitness
    needs no central entry. Silence about a capability nobody wrote a
    requirement for is not evidence of unfitness.
    """
    v = evaluate(["base", "something-nobody-declared"], REQS, GOOD)
    assert "something-nobody-declared" in v.kept


def test_a_stale_dependency_withholds_its_capability_with_the_reason():
    stale = [pkg("ada-py", "0.44.1"), pkg("occt", "7.8.1", build="novtk_h1_0"), pkg("example-mesher", "1.4.0")]
    v = evaluate(["base", "meshing"], REQS, stale)

    assert v.kept == ["meshing"]
    assert v.withheld_reasons["base"] == "ada-py 0.44.1 does not satisfy >=0.51.0"


def test_a_missing_package_withholds_and_says_it_is_missing():
    v = evaluate(["meshing"], REQS, [pkg("ada-py", "0.52.0")])
    assert "example-mesher is not installed" in v.withheld_reasons["meshing"]


def test_the_wrong_build_withholds_even_when_the_version_is_right():
    """Version-only checking would miss this.

    adapy pins occt and pythonocc-core to a build variant and requires the two
    to agree, so "right version, wrong build" is a real way to be unfit.
    """
    wrong = [pkg("ada-py", "0.52.0"), pkg("occt", "7.8.1", build="vtk_h1234_0")]
    v = evaluate(["base"], REQS, wrong)
    assert v.withheld_reasons["base"] == "occt build vtk_h1234_0 does not match novtk_*"


def test_a_package_with_no_build_string_fails_a_build_requirement():
    nobuild = [pkg("ada-py", "0.52.0"), pkg("occt", "7.8.1", build=None)]
    assert "reports no build string" in evaluate(["base"], REQS, nobuild).withheld_reasons["base"]


def test_the_reason_is_stable_across_evaluations():
    # It goes in a registry row refreshed every heartbeat. A reason that moves
    # between two equally-true answers reads as flapping.
    broken = [pkg("occt", "1.0", build="vtk_0")]
    reasons = {evaluate(["base"], REQS, broken).withheld_reasons["base"] for _ in range(5)}
    assert len(reasons) == 1


def test_a_missing_dependency_is_reported_before_a_build_mismatch():
    # Sorted, deterministic, and the more fundamental problem first: there is no
    # point reporting a build variant for a package that is not there.
    broken = [pkg("occt", "7.8.1", build="vtk_0")]
    assert "ada-py is not installed" in evaluate(["base"], REQS, broken).withheld_reasons["base"]


@pytest.mark.parametrize("packages", [None, "nope", 42, [1, 2, 3], [{"no": "name"}]])
def test_an_unreadable_package_list_withholds_rather_than_passes(packages):
    # The opposite asymmetry to a malformed document: a document we cannot read
    # gates nothing, but an environment we cannot inspect has not shown itself
    # fit. It fails the "is installed" test like any other absence.
    v = evaluate(["base"], REQS, packages)
    assert v.kept == []
    assert "not installed" in v.withheld_reasons["base"]


def test_no_requirements_at_all_keeps_everything():
    # The state of every deployment before an admin writes a document. Turning
    # the feature on must not, by itself, take a fleet offline.
    v = evaluate(["base", "meshing", "cad"], {}, [])
    assert v.kept == ["base", "meshing", "cad"]
    assert v.withheld == []


def test_capability_case_does_not_change_the_verdict():
    v = evaluate(["Base"], REQS, [pkg("ada-py", "0.44.1")])
    assert v.withheld and v.withheld[0]["capability"] == "Base"  # reported as declared


def test_the_verdict_preserves_declaration_order():
    v = evaluate(["meshing", "base"], REQS, GOOD)
    assert v.kept == ["meshing", "base"]


# --- a fully-withheld worker -----------------------------------------------


def test_withholding_everything_yields_an_empty_kept_set():
    """And the worker must NOT fall back to `base` from it.

    `_pool_capabilities` turns an empty list into `["base"]`, which is right for
    an unset env var and catastrophic for a verdict: the worker would advertise
    nothing, report `withheld: [base]`, and then quietly keep pulling base jobs.
    That is the disagreement between advertisement and subscription this whole
    design exists to prevent, so the worker bypasses that fallback rather than
    reaching it. Caught by asking what happens when the last capability goes.
    """
    v = evaluate(["base"], {"base": {"requires": {"ada-py": ">=99.0"}}}, [pkg("ada-py", "0.54.0")])
    assert v.kept == []
    assert v.withheld_reasons["base"].startswith("ada-py 0.54.0 does not satisfy")
