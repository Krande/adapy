"""What a DEXPI import could not carry, said out loud.

The procedural compiler is deliberately forgiving -- an unwireable system is skipped with a
warning, an unroutable run likewise -- and that is the wrong behaviour for a user who asked for
their P&ID. Every loss is collected here instead, attached to the model, and summarised in one
line; see :class:`DexpiImportReport`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = ["DexpiImportReport", "ImportIssue", "dexpi_import_report"]


@dataclass(frozen=True)
class ImportIssue:
    """One thing the import could not carry through to the 3D model.

    ``stage`` says where it was lost -- ``connectivity`` (the P&ID endpoint does not name something
    adapy can connect to), ``layout`` (no cell would hold it), ``wiring`` (the compiler refused the
    connection) or ``routing`` (no path was found) -- because the fix differs completely between
    them.

    ``kind="model"`` is the whole-document case rather than one lost item: a P&ID that yields no
    3D model at all, because nothing in it resolved to equipment to lay out. It carries no count of
    its own, so :meth:`DexpiImportReport.summary` states it instead of the per-item tallies, which
    are all zero in that situation and read as a clean import if left to speak alone.
    """

    kind: Literal["system", "equipment", "model"]
    name: str
    stage: str
    reason: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "stage": self.stage, "reason": self.reason}


@dataclass
class DexpiImportReport:
    """What a DEXPI import dropped, and how much of it there was.

    The procedural compiler is deliberately forgiving: an unwireable system is skipped with a
    ``logger.warning`` (``ada.topo_model.compile._wire_systems``) and an unroutable run is skipped
    inside the engine (``run_design(skip_failed=True)``). Both are the right behaviour for one bad
    spec in a large document and the wrong behaviour for a user who asked for their P&ID -- so
    every one of them is collected here, attached to ``assembly.metadata["dexpi"]["report"]``, and
    summarised in a single warning line.
    """

    issues: list[ImportIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        """True when every segment routed and every equipment landed in a cell."""
        return not self.issues

    def add(self, kind: str, name: str, stage: str, reason: str) -> None:
        self.issues.append(ImportIssue(kind=kind, name=name, stage=stage, reason=reason))  # type: ignore[arg-type]

    def of_kind(self, kind: str) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.kind == kind]

    def summary(self) -> str:
        """One line naming the counts -- what the importer logs and what an exception says.

        ``stats`` counts what *did* reach the 3D model, not what was attempted, so the total an
        issue count is reported against is ``stats + dropped`` -- otherwise a document where every
        system failed reads as "N of (a smaller number)", which parses as nonsense rather than as
        a complete failure.
        """
        systems_dropped = len(self.of_kind("system"))
        equipment_dropped = len(self.of_kind("equipment"))
        systems_total = self.stats.get("systems", 0) + systems_dropped
        equipment_total = self.stats.get("equipment", 0) + equipment_dropped
        counts = (
            f"{systems_dropped} of {systems_total} system(s) and "
            f"{equipment_dropped} of {equipment_total} equipment did not reach the 3D model"
        )

        # A document-level failure has no per-item counts behind it -- every tally above is 0 of 0,
        # which reads as a clean import. Say what actually happened instead.
        document = "; ".join(issue.reason for issue in self.of_kind("model"))
        if not document:
            return counts
        if systems_dropped or equipment_dropped:
            return f"{document}; {counts}"
        return document

    def as_dict(self) -> dict:
        return {"issues": [issue.as_dict() for issue in self.issues], "stats": dict(self.stats)}

    @classmethod
    def from_dict(cls, payload: dict | None) -> DexpiImportReport:
        payload = payload or {}
        return cls(
            issues=[
                ImportIssue(
                    kind=entry.get("kind", "system"),  # type: ignore[arg-type]
                    name=entry.get("name", ""),
                    stage=entry.get("stage", ""),
                    reason=entry.get("reason", ""),
                )
                for entry in payload.get("issues") or []
            ],
            stats=dict(payload.get("stats") or {}),
        )

    def format(self) -> str:
        """Render the report as an aligned console table, mirroring
        :func:`ada.api.systems.validation.format_port_report`."""
        if not self.issues:
            return f"DEXPI import complete: {self.summary()}."
        headers = ("Kind", "Name", "Stage", "Reason")
        rows = [(i.kind, i.name, i.stage, i.reason) for i in self.issues]
        widths = [max(len(headers[c]), *(len(r[c]) for r in rows)) for c in range(len(headers))]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        lines = [self.summary() + ":", "", fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
        lines.extend(fmt.format(*r) for r in rows)
        return "\n".join(lines)


def dexpi_import_report(model) -> str:
    """The read report of a :class:`~ada.SystemModel`, as a console table.

    Takes the model rather than a built assembly: what the *read* could not carry and what the
    *build* could not carry are different failures, and the second lives on the assembly the build
    produced (``assembly.metadata["build"]``). Anything without a report says so rather than raising.
    """
    report = getattr(model, "report", None)
    if report is None:
        return "No import report on this model."
    return report.format()
