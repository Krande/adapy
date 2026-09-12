"""Capability pools and scheduling policy: which pools this worker advertises/subscribes to,
fetch cadence and the round-robin cursor.
"""

from __future__ import annotations

import os

from ada.config import logger

from ..queue import capability_token

# How long the pull-subscriber waits per fetch round before re-issuing.
# Short enough to react to shutdown; long enough not to spam NATS.
FETCH_TIMEOUT = 5.0


# Workers fetch one message at a time — conversions are heavy and we
# don't want to hold a batch of acks open during a long-running job.
FETCH_BATCH = 1


# Maximum delivery attempts per job before we permanently mark it
# error and ack. Catches "poison pill" jobs whose conversion crashes
# the worker process (OS-level malloc / segfault) — the message gets
# redelivered each time without ever being acked, infinite-looping.
# After this many tries the worker stops attempting and acks so the
# message leaves the stream.
MAX_DELIVERIES = 3


# How many jobs in a row one pool may serve before the round-robin cursor moves
# on regardless.
#
# WHY THIS EXISTS. The pools are polled one at a time with a blocking fetch, so
# a worker serving N capabilities walks N-1 empty pools between consecutive jobs
# from the one pool that is busy — at ``_per_fetch_timeout`` each. On a combined
# worker (6 capabilities) that is ~2.1s of dead time per job: measured on a
# 907-cell sweep it was 20 minutes of a 69-minute run, and the run had been 40
# minutes on a single-pool image. Cell durations were unchanged; the pool was
# simply idle 39% of the time.
#
# Staying on a pool that just produced work removes that walk. The cap is what
# keeps it fair: without it a permanently-busy pool would starve every other
# capability this worker advertises, which is worse than the latency it fixes.
# At the default, another pool waits at most this many jobs plus one cycle.
#
# 0 or 1 restores the strict round-robin.
POOL_STREAK_LIMIT = max(1, int(os.environ.get("ADA_WORKER_POOL_STREAK_LIMIT", "8") or 8))


# While a job runs we refresh the JetStream ack deadline with
# ``msg.in_progress()`` on this cadence. A live worker keeps extending its
# lease; the moment it dies (OOM-killed pod, node failure, crash) the refreshes
# stop and JetStream redelivers within ~one ack_wait (see queue._ACK_WAIT_SECONDS)
# instead of the old fixed 30 min — so a poison/OOM job is detected and
# dead-lettered (MAX_DELIVERIES) in minutes, not ~80. Must be comfortably shorter
# than ack_wait; the conversion runs in a child process so the parent event loop
# stays free to fire these.
IN_PROGRESS_REFRESH_SECONDS = 30


# How often the worker re-publishes its registration. Also, incidentally, the
# only round-trip to the bus an IDLE worker makes: a pull fetch that times out
# with no messages is indistinguishable from a healthy quiet queue, so the
# heartbeat is the one thing whose failure means something.
BUS_HEARTBEAT_SECONDS = 15.0


# Consecutive heartbeat failures after which the worker exits non-zero instead
# of continuing to look alive.
#
# WHY THIS EXISTS. A worker whose connection is gone should either recover or
# stop; the state worth designing against is the third one, where it does
# neither. The client library is not guaranteed to fail loudly — a read loop can
# stop without raising, leaving ``is_connected`` true and every subsequent
# request timing out — and nothing else here would notice. The liveness file is
# touched by the poll loop, which keeps iterating happily against a dead
# connection, so it goes on reporting health. The registry row goes stale and
# the worker vanishes from the admin view, but the process does not care.
#
# So jobs route to a pool that no longer consumes, and sit. That is the failure
# this bounds: not the disconnect, which is ordinary, but the silence after it.
#
# The number is a compromise between the two ways of being wrong. Too low and a
# transient blip restarts a worker mid-job, losing work that would have
# recovered on its own. Too high and jobs queue against a dead pool for as long
# as it takes. At the cadence above this is a full minute of consecutive
# failures — far longer than a reconnect after a routine bus restart, short
# enough that nothing waits on it for long.
BUS_HEARTBEAT_FAILURE_LIMIT = 4


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _worker_id() -> str:
    """This worker's registry key.

    Self-asserted today: whatever ``$HOSTNAME`` says, which in k8s is the
    pod name and off-cluster is whatever the operator exports. Once NATS
    credentials pin the registry KV key (``__meta_worker__<id>``) the
    server enforces the match and this becomes an authenticated identity
    rather than a claim — see deploy/worker-trust.md.
    """
    return os.environ.get("HOSTNAME", "").strip() or f"local-{os.getpid()}"


def _declared_capabilities() -> list[str]:
    """What this worker advertises, after subtracting any disabled by env.

    ``ADA_WORKER_CAPABILITIES`` is the positive list, and normally comes from
    the IMAGE rather than from a deployment: the image is what actually carries
    the packages behind each capability, so it is the only place that can state
    the set correctly. A deployment repeating the list is a second copy of a
    fact it does not own, and it drifts -- one such copy sat a capability behind
    its image for weeks, advertising a pool that the image had and the manifest
    did not mention, with no error anywhere because a job for a pool nobody
    subscribes to is accepted and then simply never runs.

    ``ADA_WORKER_DISABLED_CAPABILITIES`` is the subtractive half, and is what a
    deployment SHOULD reach for. It is for one job: taking a misbehaving pool
    out of service without rebuilding or rolling back an image, which would
    revert every other capability and the adapy version along with it. Normally
    absent.

    Subtraction happens BEFORE the capability is advertised, not only before it
    is subscribed. Advertising a pool this worker will not serve recreates
    exactly the failure above -- the API routes to it and the job waits out its
    timeout -- so the two must never disagree.

    Compared through :func:`capability_token`, so ``Abaqus`` disables
    ``abaqus``: an operator typing a capability under incident pressure should
    not have to match case.

    Disabling everything falls back to ``base`` with a warning rather than
    leaving a worker that advertises nothing: a worker subscribed to nothing is
    not a configuration anyone wants, and scaling the deployment to zero is how
    you idle a pool.
    """
    declared = [c.strip() for c in os.environ.get("ADA_WORKER_CAPABILITIES", "base").split(",") if c.strip()]
    # An explicitly blank ADA_WORKER_CAPABILITIES means "unset", not "serve
    # nothing". Normalised here so that an empty list further down the pipeline
    # can only mean a deliberate verdict -- disabled here, or withheld by
    # qualification -- and never a variable somebody left empty.
    if not declared:
        declared = ["base"]
    raw_disabled = [c.strip() for c in os.environ.get("ADA_WORKER_DISABLED_CAPABILITIES", "").split(",") if c.strip()]
    if not raw_disabled:
        return declared

    disabled = {t for t in (capability_token(c) for c in raw_disabled) if t}
    kept = [c for c in declared if capability_token(c) not in disabled]

    dropped = [c for c in declared if capability_token(c) in disabled]
    if dropped:
        # WARNING, not info: this is a deliberate reduction in service, and the
        # symptom of forgetting to remove it later is jobs that queue forever.
        logger.warning(
            "worker: capabilities disabled by ADA_WORKER_DISABLED_CAPABILITIES: %s",
            ",".join(dropped),
        )
    # A capability may be SHARDED: one plugin addressing several pools by
    # suffixing the capability with an option value (`cad`, `cad-alpha`), so a
    # worker can hold both. Those are distinct tokens, so disabling `cad` leaves
    # `cad-alpha` serving -- and the "names nothing this worker advertises"
    # warning below does not fire, because `cad` did match. The operator gets a
    # line confirming a capability was disabled and reasonably believes the pool
    # is out of service while half of it still pulls jobs.
    #
    # Prefix-matching by default would be worse (`web3d` must not vanish because
    # somebody disabled `web`), so the shards are named instead and left for the
    # operator to disable deliberately.
    for token in sorted(disabled):
        siblings = sorted(c for c in kept if capability_token(c).startswith(f"{token}-"))
        if siblings:
            logger.warning(
                "worker: disabled %s, but %s %s still advertised — disable %s separately",
                token,
                ",".join(siblings),
                "is" if len(siblings) == 1 else "are",
                "it" if len(siblings) == 1 else "them",
            )

    unmatched = sorted(disabled - {capability_token(c) for c in declared})
    if unmatched:
        # Names nothing this worker has. Usually a typo, and a typo here is
        # silent -- the pool it was meant to stop stays up.
        logger.warning(
            "worker: ADA_WORKER_DISABLED_CAPABILITIES names %s, which this worker does not advertise",
            ",".join(unmatched),
        )
    if not kept:
        # Falling back to `base` rather than serving nothing is right for the
        # deployments this exists for -- but only if `base` was ever this
        # worker's to serve. A worker that never declared it must not ACQUIRE
        # it by subtraction: an off-cluster machine joining for one capability
        # would start pulling ordinary conversion jobs from the cluster's
        # queue on an independently-installed adapy, which is the hazard
        # ADA_WORKER_BASE_CONVERSIONS exists to prevent. Reaching it through an
        # incident switch would be a particularly unpleasant route to it.
        if any(capability_token(c) == "base" for c in declared):
            logger.warning(
                "worker: every advertised capability was disabled; falling back to 'base'. "
                "Scale the deployment to zero to idle a pool instead."
            )
            return ["base"]
        logger.warning(
            "worker: every advertised capability was disabled and this worker does not serve "
            "'base', so it will subscribe to nothing. It stays up and keeps reporting, because a "
            "worker that exits is indistinguishable from one that was never started. Scale the "
            "deployment to zero to idle a pool instead."
        )
        return []
    return kept


def _pool_capabilities(capabilities: list[str]) -> list[str]:
    """Capability pools this worker should subscribe to.

    Normalised through :func:`capability_token` and de-duplicated while
    preserving order, so a repeated or differently-cased entry in
    ``ADA_WORKER_CAPABILITIES`` cannot open two consumers on the same subject.
    Falls back to ``["base"]`` so a worker is never left subscribed to nothing.

    Deliberately the same normaliser the API uses to build the subject it
    PUBLISHES on. A sharded capability like ``cad-Site A`` has to reduce to an
    identical token on both sides, or the job is published to a subject nothing
    is subscribed to and sits in the stream looking merely slow.
    """
    pools = [t for t in (capability_token(c) for c in capabilities) if t]
    return list(dict.fromkeys(pools)) or ["base"]


def _per_fetch_timeout(n_pools: int) -> float:
    """Per-pool fetch timeout so one full round-robin cycle takes ~FETCH_TIMEOUT.

    The pools are polled one at a time (see the poll loop for why), so without
    dividing the timeout a worker's pickup latency would grow linearly with the
    number of capabilities it serves. Floored so many pools cannot degenerate
    into a busy-loop of near-instant fetches.
    """
    return max(0.5, FETCH_TIMEOUT / max(1, n_pools))


def _advance_pool_cursor(rr: int, streak: int, produced: bool, limit: int = POOL_STREAK_LIMIT) -> tuple[int, int]:
    """Where the round-robin cursor goes after one fetch — ``(rr, streak)``.

    Split out from the poll loop because it is the whole scheduling policy and
    the loop around it is untestable: everything else there needs a live
    JetStream consumer.

    An empty pool advances immediately, as it always did. A pool that produced
    work is kept, so a saturated pool re-fetches instead of walking every other
    (empty) pool first — until it has served ``limit`` jobs in a row, at which
    point it yields whether or not it still has work. That bound is what stops a
    busy pool starving the other capabilities the worker advertises.
    """
    if not produced:
        return rr + 1, 0
    streak += 1
    if streak >= max(1, limit):
        return rr + 1, 0
    return rr, streak
