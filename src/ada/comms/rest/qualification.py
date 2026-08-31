"""Deciding whether a worker is fit to serve a capability.

A capability pool is a promise: work tagged for it will be served correctly. A
worker whose environment has drifted breaks that promise *silently* — it wins
jobs and produces outdated output. ``worker.py``'s own comment records the shape
of it: an extra-capability pool built from an independent adapy "advertises the
full base converter matrix, so it wins base conversion jobs it has no business
running — and when that image is stale it produces outdated output (e.g.
non-manifold meshes)."

The guard for that today is ``ADA_WORKER_BASE_CONVERSIONS=false``, set by hand
on each foreign pool. That is a correctness property defended by remembering an
environment variable. This module inverts it: a worker advertises a capability
only if it can show its environment satisfies that capability's declared
requirements, and says why when it cannot.

Everything here is a pure function of its arguments — no environment, no I/O, no
NATS. What is fit and what is not is the part worth testing exhaustively, and it
should not need a cluster to exercise.

See ``deploy/worker-trust.md`` §4.

Why no ``packaging`` dependency
-------------------------------
The obvious implementation compares versions with ``packaging.version``. It is
not used here, and the reason is where this code runs.

``packaging`` is not in adapy's ``viewer-api`` environment. Adding it would work
for images we build, but this gate matters most on a machine somebody assembled
by hand — an off-cluster worker whose dependencies are least under our control
is exactly the one we most want gated. If the comparator needed a package that
environment might not have, the gate would have to fail open there to avoid a
self-inflicted outage, and would therefore be absent precisely where it was
supposed to be strictest.

So comparison is self-contained, deliberately narrow, and **refuses rather than
guesses**: it orders dotted numeric releases (``0.51.0``, ``7.8.1``,
``1.0.119.0``, ``2024.1``) and declines anything else. Conda versions are not
PEP 440 — epochs, build strings, and pre-release suffixes all order in ways that
differ between ecosystems — and a comparator that quietly picks an interpretation
of ``1.0.0rc1`` vs ``1.0.0`` is worse than one that says it cannot tell.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

#: KV meta key the API publishes the requirement document under, and the worker
#: reads at startup. In the meta keyspace (`__meta_` prefix) that already
#: carries the worker registry, so it needs no new plumbing and no database —
#: which matters, because the worker this gate is most for is the one least
#: likely to have a Postgres connection.
CAPABILITY_REQUIREMENTS_KEY = "capability_requirements"

__all__ = [
    "CAPABILITY_REQUIREMENTS_KEY",
    "Requirement",
    "Verdict",
    "compare_versions",
    "evaluate",
    "parse_requirements",
    "satisfies",
]

#: A comparison this module will not attempt.
INCOMPARABLE = None

_OPERATORS = ("<=", ">=", "==", "!=", "<", ">")
_NUMERIC_RELEASE = re.compile(r"^[0-9]+(\.[0-9]+)*$")


def _normalise(version: str) -> str:
    return str(version or "").strip().lstrip("vV")


def compare_versions(left: str, right: str) -> int | None:
    """``-1`` / ``0`` / ``1`` for ``left`` vs ``right``, or ``None`` when the two
    cannot be ordered with confidence.

    Ordering is attempted only for dotted numeric releases. A shorter one is
    zero-padded, so ``1.2`` == ``1.2.0`` — which is what a requirement like
    ``>=1.2`` means to the person who wrote it.

    ``None`` is not a failure to be papered over: it is the honest answer for
    ``1.0.0rc1`` against ``1.0.0``, and callers turn it into a refusal with a
    reason rather than a guess in either direction.
    """
    a, b = _normalise(left), _normalise(right)
    if a == b:
        return 0
    if not (_NUMERIC_RELEASE.match(a) and _NUMERIC_RELEASE.match(b)):
        return INCOMPARABLE
    pa = [int(x) for x in a.split(".")]
    pb = [int(x) for x in b.split(".")]
    width = max(len(pa), len(pb))
    pa += [0] * (width - len(pa))
    pb += [0] * (width - len(pb))
    return (pa > pb) - (pa < pb)


def _split_clause(clause: str) -> tuple[str, str]:
    """``">=1.2"`` -> ``(">=", "1.2")``. A bare version means ``==``."""
    text = clause.strip()
    for op in _OPERATORS:
        if text.startswith(op):
            return op, text[len(op) :].strip()
    return "==", text


def satisfies(installed: str, spec: str) -> tuple[bool, str]:
    """Does ``installed`` satisfy ``spec``? Returns ``(ok, reason_if_not)``.

    ``spec`` is comma-separated clauses, all of which must hold: ``">=1.2"``,
    ``">=1.2,<2.0"``, ``"==1.2.3"``, ``"1.2.3"``.

    An unorderable pair fails with a reason naming both versions. Equality and
    inequality still work on the exact strings, so a requirement can pin an
    otherwise-uncomparable build exactly.
    """
    for clause in (c for c in str(spec or "").split(",") if c.strip()):
        op, want = _split_clause(clause)
        if not want:
            return False, f"requirement {clause.strip()!r} names no version"
        if op == "==":
            if _normalise(installed) != _normalise(want):
                return False, f"{installed} does not satisfy {clause.strip()}"
            continue
        if op == "!=":
            if _normalise(installed) == _normalise(want):
                return False, f"{installed} does not satisfy {clause.strip()}"
            continue
        cmp = compare_versions(installed, want)
        if cmp is INCOMPARABLE:
            # Refusing beats guessing. Both versions are named so whoever reads
            # it can decide, and pin an exact version if the ordering is
            # genuinely ambiguous.
            return False, f"cannot order {installed} against {want} (requirement {clause.strip()})"
        ok = {
            ">=": cmp >= 0,
            ">": cmp > 0,
            "<=": cmp <= 0,
            "<": cmp < 0,
        }[op]
        if not ok:
            return False, f"{installed} does not satisfy {clause.strip()}"
    return True, ""


@dataclass(frozen=True)
class Requirement:
    """What one capability's output depends on.

    ``build_match`` is not decoration. adapy pins ``occt`` and ``pythonocc-core``
    to a build variant and requires the two to agree, so "right version, wrong
    build" is a real way to be unfit and a version-only check would miss it.
    """

    requires: dict[str, str] = field(default_factory=dict)
    build_match: dict[str, str] = field(default_factory=dict)


def parse_requirements(doc: object) -> dict[str, Requirement]:
    """Read the admin-authored requirement document, skipping anything malformed.

    Deliberately lenient. A malformed document is an operator typo, and the
    response to a typo must not be a worker that serves nothing — that would
    make this gate an outage mechanism. Entries that cannot be read are dropped,
    which leaves their capability ungated (fail open) exactly as if no entry had
    been written.
    """
    out: dict[str, Requirement] = {}
    if not isinstance(doc, dict):
        return out
    for capability, entry in doc.items():
        if not isinstance(capability, str) or not capability.strip() or not isinstance(entry, dict):
            continue
        requires = entry.get("requires")
        builds = entry.get("build_match")
        out[capability.strip().lower()] = Requirement(
            requires={
                str(k).strip().lower(): str(v)
                for k, v in (requires or {}).items()
                if isinstance(requires, dict) and str(k).strip()
            },
            build_match={
                str(k).strip().lower(): str(v)
                for k, v in (builds or {}).items()
                if isinstance(builds, dict) and str(k).strip()
            },
        )
    return out


def _by_name(packages: object) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(packages, list):
        return out
    for p in packages:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            out[str(p["name"]).strip().lower()] = p
    return out


@dataclass(frozen=True)
class Verdict:
    """What a worker may advertise, and why it may not advertise the rest."""

    kept: list[str]
    withheld: list[dict]

    @property
    def withheld_reasons(self) -> dict[str, str]:
        return {w["capability"]: w["reason"] for w in self.withheld}


def evaluate(
    capabilities: list[str],
    requirements: object,
    packages: object,
) -> Verdict:
    """Split ``capabilities`` into what this environment may serve and what it may not.

    The asymmetry is deliberate and is the whole design:

    * **No entry for a capability => keep it.** Backwards compatible, and a
      plugin capability that governs its own fitness needs no central entry.
      Silence about a capability nobody has written a requirement for is not
      evidence of unfitness.
    * **An entry exists and is not satisfied => withhold it, with the reason.**
      Including when the package is missing entirely, which is the single most
      likely way to be unfit.

    A withheld capability must be dropped from what the worker SUBSCRIBES to as
    well as from what it advertises. Filtering the advertisement alone would
    leave an unfit worker still holding a consumer and still winning jobs — the
    exact failure this exists to prevent, with the evidence removed.
    """
    parsed = parse_requirements(requirements)
    installed = _by_name(packages)

    kept: list[str] = []
    withheld: list[dict] = []
    for capability in capabilities:
        key = str(capability).strip().lower()
        req = parsed.get(key)
        if req is None:
            kept.append(capability)
            continue
        reason = _first_failure(req, installed)
        if reason is None:
            kept.append(capability)
        else:
            withheld.append({"capability": capability, "reason": reason})
    return Verdict(kept=kept, withheld=withheld)


def _first_failure(req: Requirement, installed: dict[str, dict]) -> str | None:
    """The first reason this environment fails ``req``, or ``None`` if it passes.

    One reason rather than all of them: it is a line in a registry row and on an
    operator's screen, and the first missing dependency is nearly always the one
    to fix. Sorted so the same environment always reports the same reason —
    a message that moves between heartbeats reads like flapping.
    """
    for name in sorted(req.requires):
        spec = req.requires[name]
        pkg = installed.get(name)
        if pkg is None:
            return f"{name} is not installed (requires {spec})"
        ok, why = satisfies(str(pkg.get("version") or ""), spec)
        if not ok:
            return f"{name} {why}"
    for name in sorted(req.build_match):
        pattern = req.build_match[name]
        pkg = installed.get(name)
        if pkg is None:
            return f"{name} is not installed (build must match {pattern})"
        build = str(pkg.get("build") or "")
        if not build:
            # A pip dist has no build string. Requiring one of a package that
            # cannot have one is a mistake in the document, but the honest
            # report is still that this environment does not satisfy it.
            return f"{name} reports no build string (build must match {pattern})"
        if not fnmatch.fnmatchcase(build, pattern):
            return f"{name} build {build} does not match {pattern}"
    return None
