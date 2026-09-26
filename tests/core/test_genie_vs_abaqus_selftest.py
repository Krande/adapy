"""Run ``verification/genie_vs_abaqus``'s self-test as part of the ordinary suite.

That package's checks need no licence, no Sestra and no Abaqus -- they run the comparator and the
closed form against deliberately broken inputs and assert it notices. Which makes them exactly the
kind of guard that rots unquestioned if nothing runs it: the package is driven by hand from a
command line, and a comparator that has quietly stopped discriminating still prints a table full of
numbers. So the suite runs them.

Each group is its own test so a failure names the part that broke, and the group's printed output
(captured by pytest) is the report: every check appears as PASS or FAIL with the numbers beside it.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

#: The number of checks below which something has been deleted rather than improved.
#:
#: A floor, not an equality, so adding a check does not mean editing this line -- but net removal
#: is caught, which is the failure mode that matters for a set of guards. Raise it when the real
#: count moves up.
MINIMUM_CHECKS = 35


@pytest.fixture(scope="module")
def selftest():
    return pytest.importorskip("verification.genie_vs_abaqus.selftest")


@pytest.mark.parametrize(
    "group",
    [
        "check_loud_failures",
        "check_agreement",
        "check_hand_check",
        "check_solver_section_idealisation",
    ],
)
def test_the_comparison_framework_checks_itself(selftest, group):
    results = getattr(selftest, group)()

    assert results, f"{group} ran no checks at all, which is not a pass"
    assert all(results), f"{results.count(False)} of {len(results)} checks failed in {group}; see the captured output"


def test_no_check_has_quietly_disappeared(selftest):
    """A guard that was deleted leaves a suite that still passes. This is what notices."""
    total = (
        len(selftest.check_loud_failures())
        + len(selftest.check_agreement())
        + len(selftest.check_hand_check())
        + len(selftest.check_solver_section_idealisation())
    )

    assert total >= MINIMUM_CHECKS, f"only {total} checks remain; MINIMUM_CHECKS is {MINIMUM_CHECKS}"
