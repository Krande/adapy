"""A report the API could not RECORD is tried again; one it REFUSED is not.

THE GAP. A pool-less worker's audit row is only ever moved by this report, so a
single lost one leaves a finished job non-terminal for ever -- which is exactly
the state the reporting mechanism exists to remove, arriving by a different door.
And the window that loses one is ordinary rather than exotic: an API rolling to a
new version, a pod restarting, a connection pool not yet up. Observed in the
field as one `refused (500)` in a worker log, against a job whose queue record
said `done`; replaying the identical request minutes later succeeded first time.

WHAT MUST NOT BE RETRIED IS THE OTHER HALF of this. A 4xx is a refusal -- a
malformed status, a scope the caller may not touch, a route the deployment does
not have -- and it will be just as wrong next time. Retrying it only buries the
reason in repetition, and the 404/405 case in particular carries a message that
tells the operator what to do; printing it three times makes it less likely to be
read, not more.
"""

from __future__ import annotations

import urllib.error

import pytest

from ada.comms.rest.worker import audit as audit_module


class _Recorder:
    """Stands in for `urlopen`, answering a scripted sequence.

    Each entry is either an int (an HTTP status to raise) or None (succeed).
    Calls beyond the script are an error rather than a silent success: a test
    that passes by running out of script is not testing the retry.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if not self.script:
            raise AssertionError(f"unscripted call {self.calls}")
        outcome = self.script.pop(0)
        if outcome is None:
            return _Response()
        if isinstance(outcome, int):
            raise urllib.error.HTTPError(req.full_url, outcome, "boom", {}, None)
        raise outcome


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


@pytest.fixture
def no_waiting(monkeypatch):
    """The backoff is real seconds in production and must not be here.

    Patched on the module rather than shortened in the source: the durations are
    a deliberate trade against blocking the job path, and a test that needed them
    small would be a test shaping production for its own convenience.
    """
    slept: list[float] = []
    monkeypatch.setattr(audit_module.time, "sleep", lambda s: slept.append(s))
    return slept


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(audit_module, "_rest_source_nodes_config", lambda: ("http://api.invalid", "tok"))


async def _report():
    return await audit_module._report_job_status_over_api("job-1", {"status": "done"})


@pytest.mark.asyncio
async def test_a_500_is_tried_again_and_the_second_attempt_lands(configured, no_waiting, monkeypatch):
    """The observed failure, and the whole point: the row is recorded after all."""
    calls = _Recorder([500, None])
    monkeypatch.setattr("urllib.request.urlopen", calls)
    assert await _report() is True
    assert calls.calls == 2
    assert no_waiting == [audit_module._REPORT_BACKOFF_S[0]]


@pytest.mark.asyncio
async def test_it_gives_up_after_the_budget_rather_than_forever(configured, no_waiting, monkeypatch):
    """Bounded on purpose. This is awaited on the job path, so a worker that kept
    retrying would stop taking work in order to finish talking about work it had
    already done."""
    calls = _Recorder([500] * audit_module._REPORT_ATTEMPTS)
    monkeypatch.setattr("urllib.request.urlopen", calls)
    assert await _report() is False
    assert calls.calls == audit_module._REPORT_ATTEMPTS
    assert len(no_waiting) == audit_module._REPORT_ATTEMPTS - 1, "no sleep after the last attempt"


@pytest.mark.asyncio
async def test_a_transport_failure_is_retried_too(configured, no_waiting, monkeypatch):
    """Never reached the API at all -- a reset connection, a DNS blip, a timeout.
    Retryable for the same reason a 5xx is, and for a pool-less worker the more
    common of the two."""
    calls = _Recorder([urllib.error.URLError("connection reset"), None])
    monkeypatch.setattr("urllib.request.urlopen", calls)
    assert await _report() is True
    assert calls.calls == 2


@pytest.mark.parametrize("code", [400, 403, 404, 405, 409, 422])
@pytest.mark.asyncio
async def test_a_4xx_is_final_and_attempted_exactly_once(configured, no_waiting, monkeypatch, code):
    """A refusal will be just as wrong next time. 404/405 matters most here: it
    carries the message saying the deployment predates the route, and printing
    that three times makes it less likely to be read rather than more."""
    calls = _Recorder([code])
    monkeypatch.setattr("urllib.request.urlopen", calls)
    assert await _report() is False
    assert calls.calls == 1
    assert no_waiting == [], "a refusal must not cost a backoff either"


@pytest.mark.asyncio
async def test_a_first_attempt_that_lands_costs_nothing_extra(configured, no_waiting, monkeypatch):
    """The overwhelmingly common path stays one request and no sleep."""
    calls = _Recorder([None])
    monkeypatch.setattr("urllib.request.urlopen", calls)
    assert await _report() is True
    assert calls.calls == 1
    assert no_waiting == []


@pytest.mark.asyncio
async def test_with_no_api_configured_nothing_is_attempted(no_waiting, monkeypatch):
    """A worker that cannot reach an API is not a worker whose reports failed, and
    it must not spend the budget discovering that once per job."""
    monkeypatch.setattr(audit_module, "_rest_source_nodes_config", lambda: None)
    calls = _Recorder([])
    monkeypatch.setattr("urllib.request.urlopen", calls)
    assert await _report() is False
    assert calls.calls == 0
