"""Giving up on the bus is not an option any of these processes has.

``nats-py`` stops reconnecting after ``max_reconnect_attempts`` (60) tries at
``reconnect_time_wait`` (2s) — roughly two minutes. Giving up is not a pause: it
raises ``NoServersError``, calls ``close()``, and every later operation raises
``ConnectionClosedError: nats: connection closed`` for the life of the process.
Nothing retries a closed client, and a closed client is what the process is left
holding.

Two minutes is shorter than an ordinary restart of the bus, so the default turns
a routine restart into a permanent outage for any long-lived process that was
not restarted alongside it. An API in that state still serves HTTP — so it looks
healthy to a liveness probe while failing every request that touches the queue,
and nothing restarts it either.

These tests pin the policy, because it is invisible at the call site: nothing
about ``connect()`` reveals which of the two behaviours it got.
"""

import inspect

from ada.comms.rest.config import QueueConfig
from ada.comms.rest.queue import JobQueue


def _queue(**over) -> JobQueue:
    base = dict(url="nats://localhost:4222", stream="S", subject="subj", kv_bucket="kv", durable="d")
    base.update(over)
    return JobQueue(QueueConfig(**base))


def test_the_client_never_stops_trying_to_reach_the_bus():
    # Negative is nats-py's "never stop reconnecting". Zero and the default 60
    # both eventually close the client permanently, which is the failure.
    assert JobQueue._connection_policy_options()["max_reconnect_attempts"] < 0


def test_coming_and_going_is_reported():
    # A disconnect that heals should still leave a trace: "how long was it
    # down" is not answerable afterwards if nothing said so at the time.
    policy = JobQueue._connection_policy_options()
    for cb in ("disconnected_cb", "reconnected_cb", "closed_cb"):
        assert inspect.iscoroutinefunction(policy[cb]), cb


def test_policy_is_kept_out_of_the_credential_mapping():
    # The two are separate on purpose. _connect_options answers "what did the
    # operator configure" and its tests assert that mapping is exact; folding
    # always-on policy into it would break them for no reason and blur what
    # each is for.
    assert _queue()._connect_options("client") == {"name": "client"}


def test_policy_applies_whatever_the_credentials_are():
    # Merged at connect time, so no auth branch can drop it — the kind of
    # setting that otherwise gets lost when a new branch is added above it.
    for over in (
        {},
        {"url": "ws://localhost:8080"},
        {"url": "wss://example.invalid"},
        {"user": "u", "password": "p"},
        {"token": "t"},
    ):
        q = _queue(**over)
        merged = {**q._connect_options("client"), **q._connection_policy_options()}
        assert merged["max_reconnect_attempts"] < 0, over
