"""An Abaqus job can wait for license tokens instead of failing its checkout.

The pool is shared, so jobs run side by side (a verification build's solver matrix) each take a slot:
admitted when the pool has room for them on top of every job this process already runs -- counted
from admission, before their checkout shows on the server.
"""

import pytest

from ada.fem.formats.abaqus import licensing
from ada.fem.formats.abaqus.licensing import (
    AbaqusTokens,
    abaqus_license_slot,
    parse_abaqus_tokens,
)

RU = """
Users of abaqus:  (Total of 25 licenses issued;  Total of 19 licenses in use)

  "abaqus" v62.7, vendor: ABAQUSLM, expiry: 31-dec-2026
  floating license

    ofssumal ABLAPP124 ABLXENFET021 (v62.7) (abllic022/27006 280), start Thu 10/1 15:46, 8 licenses
    ofssumal ABLAPP124 ABLXENFET021 (v62.7) (abllic022/27006 1062), start Thu 10/1 16:57, 8 licenses
    ofskrand ABLNOF8VY014 ABLNOF8VY014 (v62.7) (abllic022/27006 982), start Thu 10/1 20:13, 3 licenses

Users of cae:  (Total of 4 licenses issued;  Total of 1 license in use)

    ofsbkaku ABLNO69VV4X3 ABLNO69VV4X3 (v62.5) (abllic022/27006 473), start Thu 10/1 14:49
"""


def test_parses_the_abaqus_feature_and_our_own_checkouts():
    tokens = parse_abaqus_tokens(RU, user="ofskrand", host="ABLNOF8VY014")
    assert tokens == AbaqusTokens(issued=25, in_use=19, mine=3)
    assert tokens.others == 16


def test_another_users_checkout_on_our_host_is_theirs():
    assert parse_abaqus_tokens(RU, user="someone", host="ABLNOF8VY014").mine == 0


def test_no_abaqus_feature_is_none():
    assert parse_abaqus_tokens("lmgrd is not running") is None


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def sleep(self, s):
        self.slept.append(s)
        self.now += s

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(licensing, "_tokens_reserved", 0)
    for name in ("ADA_ABAQUS_LICENSE_WAIT_S", "ADA_ABAQUS_JOB_TOKENS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ADA_ABAQUS_LICENSE_WAIT_S", "600")
    monkeypatch.setenv("ADA_ABAQUS_JOB_TOKENS", "3")


def test_off_unless_asked_for():
    def status():
        raise AssertionError("the server must not be asked")

    with abaqus_license_slot(status=status) as waited:
        assert waited is False


def test_two_jobs_admitted_together_count_each_other(enabled):
    """6 tokens not held by others: room for two of our jobs, not three. The server still shows
    none of ours checked out -- the second and third are counted by their slots."""
    clock = _Clock()
    free6 = lambda: AbaqusTokens(issued=25, in_use=19, mine=0)  # noqa: E731
    with abaqus_license_slot(status=free6, sleep=clock.sleep, clock=clock) as w1:
        with abaqus_license_slot(status=free6, sleep=clock.sleep, clock=clock) as w2:
            assert (w1, w2) == (False, False)
            assert licensing._tokens_reserved == 6
            # A third has no room until one of the two ends; here it waits out its time and goes.
            with abaqus_license_slot(status=free6, sleep=clock.sleep, clock=clock) as w3:
                assert w3 is True
    assert licensing._tokens_reserved == 0
    assert 600 <= sum(clock.slept) < 620


def test_a_waiting_job_goes_when_others_free_tokens(enabled):
    readings = iter([AbaqusTokens(25, 24, 0), AbaqusTokens(25, 23, 0), AbaqusTokens(25, 22, 0)])
    clock = _Clock()
    with abaqus_license_slot(status=lambda: next(readings), sleep=clock.sleep, clock=clock) as waited:
        assert waited is True
    assert len(clock.slept) == 2


def test_our_own_checkouts_are_not_counted_twice(enabled):
    """Once our running job's checkout shows on the server (mine=3), it is still one slot -- not
    3 tokens of 'others' plus a slot."""
    clock = _Clock()
    with abaqus_license_slot(status=lambda: AbaqusTokens(25, 19, 0), sleep=clock.sleep, clock=clock):
        with abaqus_license_slot(status=lambda: AbaqusTokens(25, 22, 3), sleep=clock.sleep, clock=clock) as w:
            assert w is False


def test_an_unreachable_server_does_not_block(enabled):
    clock = _Clock()
    with abaqus_license_slot(status=lambda: None, sleep=clock.sleep, clock=clock) as waited:
        assert waited is False
    assert clock.slept == []


def test_a_job_is_planned_at_five_tokens_by_default(monkeypatch):
    """Without a deck to read, a job is planned at the larger, static-analysis count: four free
    tokens are not enough room."""
    monkeypatch.setenv("ADA_ABAQUS_LICENSE_WAIT_S", "600")
    readings = iter([AbaqusTokens(25, 21, 0), AbaqusTokens(25, 20, 0)])  # 4, then 5 not held by others
    clock = _Clock()
    with abaqus_license_slot(status=lambda: next(readings), sleep=clock.sleep, clock=clock) as waited:
        assert waited is True
    assert len(clock.slept) == 1


def test_running_jobs_are_reserved_at_their_own_count(monkeypatch):
    """A 5-token static job is running; an eigen job asking for 3 must see it as 5, not 3. Eight
    tokens are not held by others: 5 + 3 fit, 5 + 5 do not."""
    monkeypatch.setenv("ADA_ABAQUS_LICENSE_WAIT_S", "600")
    clock = _Clock()
    pool = lambda: AbaqusTokens(issued=25, in_use=17, mine=0)  # noqa: E731
    with abaqus_license_slot(tokens=5, status=pool, sleep=clock.sleep, clock=clock):
        with abaqus_license_slot(tokens=3, status=pool, sleep=clock.sleep, clock=clock) as eigen:
            assert eigen is False
            assert licensing._tokens_reserved == 8
        with abaqus_license_slot(tokens=5, status=pool, sleep=clock.sleep, clock=clock) as static:
            assert static is True  # waited out its time: 5 + 5 > 8
    assert licensing._tokens_reserved == 0


@pytest.mark.parametrize(
    "keywords, tokens",
    [
        (["*Step, name=Eigen, perturbation", "*Frequency, eigensolver=Lanczos"], licensing.EIGEN_JOB_TOKENS),
        (["*Step, name=static", "*Static"], licensing.GENERAL_JOB_TOKENS),
        (["*Step, name=a", "*Static", "*Step, name=b, perturbation", "*FREQUENCY"], licensing.GENERAL_JOB_TOKENS),
        (["** a comment mentioning *Frequency", "*Node"], licensing.GENERAL_JOB_TOKENS),
    ],
)
def test_the_deck_says_how_many_tokens_a_job_needs(tmp_path, keywords, tokens):
    """An eigenfrequency analysis holds 3 tokens, a static one 5; a deck with any non-frequency
    step is planned at 5, as is one with no recognisable step at all."""
    deck = tmp_path / "job.inp"
    deck.write_text("\n".join(keywords) + "\n")
    assert licensing.abaqus_job_tokens(deck) == tokens


def test_an_unreadable_deck_is_planned_at_the_larger_count(tmp_path):
    assert licensing.abaqus_job_tokens(tmp_path / "missing.inp") == licensing.GENERAL_JOB_TOKENS


def test_the_slot_is_released_when_the_job_fails(enabled):
    with pytest.raises(RuntimeError):
        with abaqus_license_slot(status=lambda: AbaqusTokens(25, 0, 0)):
            raise RuntimeError("abaqus crashed")
    assert licensing._tokens_reserved == 0
