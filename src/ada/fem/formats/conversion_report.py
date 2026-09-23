"""What a conversion could not carry across, collected while it runs.

Converting between FE formats is lossy: every target lacks something the source can say. The
rule this module exists to serve is:

    Raise only when no correct subset of the input can be written. Otherwise omit the
    construct, record it here, and carry on.

"Otherwise" splits three ways:

``omitted``
    The construct produces nothing in the output — an unsupported constraint or element type, a
    keyword the reader has no handler for, a reference that cannot be resolved, a tie node
    outside tolerance. The model that comes out is missing something the model going in had.
``approximated``
    Something *is* written, but it is not the same physics — a distributing coupling written as a
    rigid arm, a tie resolved to the nearest node. These carry a *measure* of the approximation
    in ``details`` (distances, counts), because an approximation nobody can size is
    indistinguishable from a guess.
``suspect``
    Written exactly as declared, and valid in the target -- but the declaration is probably a
    modelling error, so the output is faithful to a deck that is itself wrong. Two constraints
    making the same node's DOF dependent is the case this exists for: Sesam *sums* linear
    dependencies, so two records each meaning ``u_s = u_m`` silently produce
    ``u_s = u_m1 + u_m2``. Nothing was lost in the conversion and nothing was approximated, yet a
    human must look at it -- which is why it is neither of those and why ``--strict`` fails on it.
``note``
    Written exactly as declared, but worth a human's attention, or plain inventory.

What stays fatal: an output that would contradict itself, or an input that cannot be parsed at
all. Duplicate node or element ids, a DOF both fixed and linearly dependent, a malformed
``*Node`` block. Those are two *valid* readings colliding in the target, and silently picking one
changes the model.

Two failure modes this module is shaped to avoid:

*Losing an omission.* Every finding logs as it happens, whether or not anyone is collecting, so
a library user who never opens a collector still sees exactly what they see today.

*Burying an omission in a flood.* A finding is recorded per *construct*, never per node, and
repeats of the same ``(kind, stage, keyword, reason)`` increment a count instead of logging
again. Ten thousand dropped elements are one line saying ten thousand — a log nobody can scroll
hides an omission just as effectively as no log at all.

Typical use, in a writer::

    conversion_report.current().omitted(
        "sesam writer", "*TIE", constraint.name, "no Sesam representation", n_nodes=len(nodes)
    )

and around a conversion::

    with conversion_report.collect() as report:
        a = ada.from_fem("in.inp", "abaqus")
        a.to_fem("out", "sesam")
    print(report.summary())
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import datetime
import json
import pathlib
from typing import Any, Iterator

from ada.config import logger

OMITTED = "omitted"
SUSPECT = "suspect"
APPROXIMATED = "approximated"
NOTE = "note"

#: Worst-first. ``status`` and ``summary`` both order by this.
KINDS = (OMITTED, SUSPECT, APPROXIMATED, NOTE)

#: The kinds a human has to make a decision about. These are what make ``ada convert`` leave a
#: report file behind, and what ``--strict`` fails on (bar approximations, which are routine).
ACTIONABLE = (OMITTED, SUSPECT, APPROXIMATED)

STATUS_COMPLETED = "COMPLETED"
STATUS_WITH_APPROXIMATIONS = "COMPLETED_WITH_APPROXIMATIONS"
STATUS_WITH_SUSPECTS = "COMPLETED_WITH_SUSPECT_INPUT"
STATUS_WITH_OMISSIONS = "COMPLETED_WITH_OMISSIONS"

#: How many further subjects a deduplicated finding remembers by name. The count is exact
#: regardless; this only decides how many examples an engineer gets without opening the deck.
MAX_OTHER_SUBJECTS = 10


def _jsonable(value: Any) -> Any:
    """Coerce ``value`` into something :mod:`json` will take.

    ``details`` are numbers measured during a conversion, so they arrive as numpy scalars and
    arrays more often than not. A report that raises ``TypeError`` at ``write_json`` — after the
    deck is already on disk — would be worse than useless, so nothing here is allowed to fail.
    """
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    for attr in ("tolist", "item"):  # numpy arrays, then numpy scalars
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return _jsonable(method())
            except (TypeError, ValueError):
                break
    return str(value)


@dataclasses.dataclass
class Finding:
    """One thing a conversion could not carry across faithfully.

    ``subject`` names the first construct this finding was raised for; when the same
    ``(kind, stage, keyword, reason)`` recurs, ``count`` grows and further names land in
    ``other_subjects`` up to :data:`MAX_OTHER_SUBJECTS`.
    """

    kind: str
    stage: str
    keyword: str
    subject: str
    reason: str
    count: int = 1
    details: dict = dataclasses.field(default_factory=dict)
    other_subjects: list = dataclasses.field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str, str]:
        """What makes two findings "the same" for counting purposes. Deliberately not ``subject``."""
        return (self.kind, self.stage, self.keyword, self.reason)

    def to_dict(self) -> dict:
        out = {
            "kind": self.kind,
            "stage": self.stage,
            "keyword": self.keyword,
            "subject": self.subject,
            "reason": self.reason,
            "count": self.count,
        }
        if self.other_subjects:
            out["other_subjects"] = [str(s) for s in self.other_subjects]
        if self.details:
            out["details"] = _jsonable(self.details)
        return out

    def one_line(self) -> str:
        """``*TIE (3x): Constraint-2 - no Sesam representation`` — for the log and the summary."""
        times = f" ({self.count}x)" if self.count > 1 else ""
        subject = f": {self.subject}" if self.subject else ""
        details = ", ".join(f"{k}={_brief(v)}" for k, v in self.details.items())
        return f"{self.keyword}{times}{subject} - {self.reason}" + (f" [{details}]" if details else "")


def _brief(value: Any) -> str:
    """A number or short list as it should read inside a one-line log message."""
    value = _jsonable(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, list):
        shown = ", ".join(_brief(v) for v in value[:5])
        return f"[{shown}{', ...' if len(value) > 5 else ''}]"
    return str(value)


class ConversionReport:
    """The findings of one conversion.

    Created by :func:`collect`. A report obtained from :func:`current` outside any collector is a
    throwaway that logs and is discarded, so call sites need no ``if report is not None``.
    """

    def __init__(self):
        self._findings: list[Finding] = []
        self._by_key: dict[tuple[str, str, str, str], Finding] = {}

    # ------------------------------------------------------------------ recording

    def omitted(self, stage: str, keyword: str, subject: str, reason: str, count: int = 1, **details) -> Finding:
        """Record that something in the input produced nothing in the output."""
        return self._add(OMITTED, stage, keyword, subject, reason, count, details)

    def approximated(self, stage: str, keyword: str, subject: str, reason: str, count: int = 1, **details) -> Finding:
        """Record that something was written, but with different physics.

        Pass a measure of the difference in ``details`` — a distance, a dropped weight, a count.
        """
        return self._add(APPROXIMATED, stage, keyword, subject, reason, count, details)

    def suspect(self, stage: str, keyword: str, subject: str, reason: str, count: int = 1, **details) -> Finding:
        """Record that the output is faithful but the input looks like a modelling error."""
        return self._add(SUSPECT, stage, keyword, subject, reason, count, details)

    def note(self, stage: str, keyword: str, subject: str, reason: str, count: int = 1, **details) -> Finding:
        """Record something written faithfully that a human should still look at, or inventory."""
        return self._add(NOTE, stage, keyword, subject, reason, count, details)

    def _add(self, kind, stage, keyword, subject, reason, count, details) -> Finding:
        subject = "" if subject is None else str(subject)
        finding = Finding(kind, stage, keyword, subject, reason, count, dict(details))
        existing = self._by_key.get(finding.key)
        if existing is not None:
            # Seen before: grow the count, remember a few more names, and stay quiet. The first
            # occurrence already said this on the console.
            existing.count += count
            if subject and subject != existing.subject and len(existing.other_subjects) < MAX_OTHER_SUBJECTS:
                if subject not in existing.other_subjects:
                    existing.other_subjects.append(subject)
            for key, value in finding.details.items():
                existing.details.setdefault(key, value)
            return existing

        self._findings.append(finding)
        self._by_key[finding.key] = finding
        self._log(finding)
        return finding

    @staticmethod
    def _log(finding: Finding) -> None:
        # Formatted eagerly, into one string. This matters: ``DuplicateFilter`` compares the
        # *unformatted* ``record.msg``, so a lazy ``logger.warning("%s: %s", kind, text)`` would
        # give every finding in the process the same ``msg`` and the filter would collapse a
        # hundred different omissions into "the previous message is repeated 5 times".
        #
        # ``suppress_filters`` is deliberately NOT set: inside a collector this class has already
        # deduplicated, so the filter never sees a repeat; outside one, where repeats do reach it,
        # its flood protection is exactly what we want.
        message = f"[{finding.kind.upper()}] {finding.stage}: {finding.one_line()}"
        if finding.kind == NOTE:
            logger.info(message)
        else:
            logger.warning(message)

    # ------------------------------------------------------------------ reading

    @property
    def findings(self) -> list[Finding]:
        return list(self._findings)

    def of_kind(self, kind: str) -> list[Finding]:
        return [f for f in self._findings if f.kind == kind]

    @property
    def has_omissions(self) -> bool:
        return any(f.kind == OMITTED for f in self._findings)

    @property
    def has_suspects(self) -> bool:
        return any(f.kind == SUSPECT for f in self._findings)

    @property
    def needs_a_decision(self) -> bool:
        """Whether anything here asks a human to look. Drives the report file and ``--strict``."""
        return any(f.kind in ACTIONABLE for f in self._findings)

    @property
    def has_approximations(self) -> bool:
        return any(f.kind == APPROXIMATED for f in self._findings)

    @property
    def status(self) -> str:
        if self.has_omissions:
            return STATUS_WITH_OMISSIONS
        if self.has_suspects:
            return STATUS_WITH_SUSPECTS
        if self.has_approximations:
            return STATUS_WITH_APPROXIMATIONS
        return STATUS_COMPLETED

    def counts(self) -> dict[str, int]:
        """Total occurrences per kind — ``count`` summed, not findings counted."""
        return {kind: sum(f.count for f in self.of_kind(kind)) for kind in KINDS if self.of_kind(kind)}

    def summary(self) -> str:
        """A few lines for a terminal. Worst first; the JSON sidecar carries everything.

        Notes are *counted* here, not listed. A note is inventory -- a keyword census carries the
        deck's whole keyword table in its details -- and printing that on every conversion is the
        console flood this reporting exists to prevent. A terminal should carry the things that
        need a decision: what was dropped, and what was written differently.
        """
        actionable = [f for kind in ACTIONABLE for f in self.of_kind(kind)]
        n_notes = sum(f.count for f in self.of_kind(NOTE))

        if not actionable:
            line = f"{self.status}: nothing was omitted or approximated."
            return f"{line} {n_notes} note(s) recorded." if n_notes else line

        counts = self.counts()
        head = ", ".join(f"{counts[k]} {k}" for k in KINDS if k in counts)
        lines = [f"{self.status} ({head})"]
        lines += [f"  {f.kind:13} {f.one_line()}" for f in actionable]
        if n_notes:
            lines.append(f"  {'note':13} {n_notes} recorded; see the conversion report")
        return "\n".join(lines)

    def to_dict(self, **header) -> dict:
        from ada import __version__

        return {
            "ada_version": __version__,
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            **header,
            "status": self.status,
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self._findings],
        }

    def write_json(self, path, **header) -> pathlib.Path:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(**header), indent=2) + "\n", encoding="utf-8")
        return path


_active: contextvars.ContextVar[ConversionReport | None] = contextvars.ContextVar("ada_conversion_report", default=None)


def current() -> ConversionReport:
    """The report being collected, or a throwaway that only logs.

    Never ``None``, so a writer needs no guard around reporting. Outside a :func:`collect` block
    each call gets a fresh instance: the finding is logged and forgotten, which is exactly the
    behaviour these call sites had before they reported anything.
    """
    report = _active.get()
    return report if report is not None else ConversionReport()


@contextlib.contextmanager
def collect() -> Iterator[ConversionReport]:
    """Collect findings raised inside the block.

    Nests; the enclosing collector is restored on the way out, including when the block raises —
    a conversion that dies partway through has still learnt something worth reporting.
    """
    report = ConversionReport()
    token = _active.set(report)
    try:
        yield report
    finally:
        _active.reset(token)
