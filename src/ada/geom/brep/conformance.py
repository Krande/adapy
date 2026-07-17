"""Conformance harness: measure a geometry-derived body against the imported truth.

The import producer gives a ground-truth store (identity-preserving read of the
source ACIS). Any other producer — chiefly the geometry weld that builds an ACIS
body from adapy objects — can be scored against it with :func:`store_equivalence`,
which classifies every mismatch as Class 1 (weld fragmentation / wrong sharing),
Class 2 (missing split/imprint) or Class 3 (non-derivable). This is the oracle that
drives hardening the weld: "done" is Class 1 + Class 2 → 0.

`derive_store_from_part` runs the current SAT writer and re-parses its output, so it
scores whatever the production weld currently emits — no separate re-implementation
to drift out of sync.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ada.geom.brep.diff import StoreDiff, store_equivalence

if TYPE_CHECKING:
    from ada.geom.brep import BRepStore


def derive_store_from_part(part) -> BRepStore:
    """The store the current geometry weld produces for ``part`` (SAT round-tripped
    back through the import parser)."""
    from ada.cadit.sat.read.to_brep import sat_store_to_brep
    from ada.cadit.sat.store import SatReaderFactory
    from ada.cadit.sat.write.writer import part_to_sat_writer

    sw = part_to_sat_writer(part)
    text = sw.to_str()
    with tempfile.TemporaryDirectory() as td:
        sat_path = Path(td) / "derive.sat"
        sat_path.write_text(text)
        f = SatReaderFactory(sat_path)
        f.load_sat_data_from_file()
        return sat_store_to_brep(f.sat_store)


def conformance(genie_xml) -> StoreDiff:
    """Score the weld against the imported truth for a Genie concept XML.

    Reads the model twice: once into the identity-preserving import store (truth),
    once into adapy objects that the weld re-derives a store from. Returns the
    classified :class:`StoreDiff`.
    """
    import ada
    from ada.cadit.sat.read.to_brep import genie_xml_to_brep

    truth = genie_xml_to_brep(genie_xml)
    part = ada.from_genie_xml(str(genie_xml))  # no store → weld path
    derived = derive_store_from_part(part)
    return store_equivalence(truth, derived)
