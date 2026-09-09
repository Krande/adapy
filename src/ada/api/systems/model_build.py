"""Turn a :class:`~ada.api.systems.model.SystemModel` into a 3D :class:`~ada.Assembly`.

Everything the build owns lives here: generating the decks, placing the equipment on them,
compiling the structure, routing the runs, and -- when asked -- feeding the routing failures back
into the layout and doing it again.

Kept out of ``model.py`` so the container stays a description of what a plant *is*, with no
knowledge of the engine that turns it into geometry.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada import Assembly
    from ada.topo_model.build_spec import ProceduralBuildSpec

    from .model import SystemModel

__all__ = ["build_assembly"]


def build_assembly(model: "SystemModel", spec: "ProceduralBuildSpec") -> "Assembly":
    """Build ``model`` into a routed 3D assembly under ``spec``.

    The procedural document is produced *here*, not stored on the model, because the decks and the
    equipment coordinates in it are products of ``spec`` -- ask for different deck bounds and you
    get a different document from the same model.

    Build-stage gaps (a system the compiler could not wire, a run it could not route, equipment no
    cell would hold) are collected onto the returned assembly's report rather than left to a log
    line. Read-stage gaps stay on the model; they are a different failure with a different fix.
    """
    from ada.topo_model.compile import build_procedural_assembly

    if model.procedural_factory is None:
        raise ValueError(
            f"{model!r} has no procedural_factory, so there is nothing to build from. A reader "
            "such as ada.from_dexpi sets one; a hand-built SystemModel must supply it."
        )

    doc, catalog = model.procedural_factory(spec)
    doc["design_rules"] = spec.design_rules
    if not spec.route:
        doc["systems"] = []

    # A model with nothing to lay out -- an instrumentation-only sheet, or one whose items all
    # resolved to something other than equipment -- produces a layout with no decks in it, and
    # ProceduralBuilder rightly refuses to compile an empty document. That is a property of the
    # model rather than a fault, so it is reported like any other build gap instead of raising a
    # bare ValueError at the caller for a P&ID that read perfectly well.
    if not (doc.get("spaces") or doc.get("loft_members")):
        return _nothing_to_build(model)

    relocations = None
    if spec.relocate:
        doc, relocations = _relocate(doc, catalog, spec)

    with _build_warnings() as records:
        assembly = build_procedural_assembly(
            doc,
            name=model.name,
            equipment_resolver=catalog.get,
            blueprint_name=spec.blueprint,
            lod=spec.lod,
            detailing=spec.detailing,
            detailing_options=spec.detailing_options,
        )

    assembly.metadata["build"] = _build_report(assembly, doc, records, relocations)
    return assembly


def _relocate(doc: dict, catalog: dict, spec: "ProceduralBuildSpec") -> tuple[dict, dict]:
    """Close the loop between the router's failures and the layout, and say what moved."""
    from ada.topo_model.relocate import relocate_doc

    # design_rules is left None on purpose: propose_relocations resolves the slug off the document
    # itself, and that parameter takes an already-resolved ruleset rather than the slug.
    moved, record = relocate_doc(
        doc,
        equipment_resolver=catalog.get,
        max_passes=2 if spec.relocate is True else max(1, int(spec.relocate)),
    )
    if record["applied"]:
        logger.info(
            "build: relocated %d equipment over %d pass(es) to clear %d unroutable run(s)",
            len(record["applied"]),
            record["passes"],
            record["baseline_problems"],
        )
    return moved, record


@contextlib.contextmanager
def _build_warnings():
    """Collect the warnings the procedural compiler drops systems with.

    ``_wire_systems`` and ``run_design(skip_failed=True)`` both report a lost run with nothing but
    ``logger.warning``, which is right for a compiler and useless to someone who asked for their
    plant. adapy's logger does not propagate to the root, so this attaches to it directly rather
    than going through ``logging.capture``.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    ada_logger = logging.getLogger("ada")
    handler = _Collector(level=logging.WARNING)
    previous = ada_logger.level
    ada_logger.addHandler(handler)
    if previous > logging.WARNING or previous == logging.NOTSET:
        ada_logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        ada_logger.removeHandler(handler)
        ada_logger.setLevel(previous)


def _build_report(assembly, doc: dict, records: list, relocations: dict | None) -> dict:
    """What the build did, and what it could not carry -- attached to the assembly it produced.

    Deliberately separate from the read report on the model: "the P&ID never said where this run
    goes" and "the router could not find a path" are different failures with different fixes, and
    one list mixing them serves neither reader.
    """
    wanted = {spec.get("NAME") for spec in doc.get("systems") or [] if isinstance(spec, dict)}
    built = {system.name for system in assembly.systems}

    # Layout gaps come from the conversion, because that is where the decks are generated -- and
    # they are build gaps, not read ones: whether a piece of equipment fits depends entirely on the
    # deck bounds this build was given.
    issues = [
        dict(entry)
        for entry in ((doc.get("dexpi") or {}).get("report") or {}).get("issues") or []
        if entry.get("stage") == "layout"
    ]
    for name in sorted(wanted - built):
        reason = next(
            (record.getMessage() for record in records if name in record.getMessage()),
            "the compiler did not build this system and gave no reason",
        )
        issues.append({"kind": "system", "name": name, "stage": "build", "reason": reason})

    report = {
        "issues": issues,
        "stats": {"systems": len(built), "equipment": len(doc.get("equipments") or [])},
    }
    if relocations is not None:
        report["relocations"] = relocations
    if issues:
        logger.warning(
            "build: %d of %d system(s) did not reach the 3D model (see assembly.metadata['build'])",
            len(issues),
            len(wanted),
        )
    return report


def _nothing_to_build(model: "SystemModel") -> "Assembly":
    """An empty assembly plus the reason it is empty, for a model with no equipment to place."""
    import ada

    reason = "no equipment resolved, so the generated layout has no decks and no 3D model was built"
    assembly = ada.Assembly(name=model.name)
    assembly.metadata["build"] = {
        "issues": [{"kind": "model", "name": model.name, "stage": "layout", "reason": reason}],
        "stats": {"systems": 0, "equipment": 0},
    }
    logger.warning("build: %s (see assembly.metadata['build'])", reason)
    return assembly
