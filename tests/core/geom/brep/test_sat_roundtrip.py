"""Stage 4: BRepStore -> SAT text -> BRepStore must diff clean.

The strongest self-contained proof that the neutral store is a *complete*
description of a body: serialise it, re-parse it, and require the differ to find
zero difference. No Genie, no OCC — pure store<->SAT fidelity.
"""

import pytest

from ada.cadit.sat.read.to_brep import sat_store_to_brep
from ada.cadit.sat.store import SatReaderFactory
from ada.cadit.sat.write.from_brep import brep_store_to_sat_text
from ada.geom.brep.diff import store_equivalence


@pytest.fixture
def curved_plates_store(fem_files):
    xml = (fem_files / "sesam/curved_plates.xml").resolve().absolute()
    sat_path = xml.with_suffix(".sat")
    if not sat_path.exists():
        from ada.cadit.gxml.sat_helpers import write_xml_sat_text_to_file

        write_xml_sat_text_to_file(xml_file=xml, out_file=sat_path)
    f = SatReaderFactory(sat_path)
    f.load_sat_data_from_file()
    return sat_store_to_brep(f.sat_store)


def test_store_sat_roundtrip_is_equivalent(curved_plates_store, tmp_path):
    store = curved_plates_store
    assert store.summary()["faces"] > 0

    text = brep_store_to_sat_text(store)
    assert text.startswith("2000 0 1 0")  # ACIS header
    assert "End-of-ACIS-data" in text

    sat_path = tmp_path / "roundtrip.sat"
    sat_path.write_text(text)
    f = SatReaderFactory(sat_path)
    f.load_sat_data_from_file()
    store2 = sat_store_to_brep(f.sat_store)

    diff = store_equivalence(store, store2)
    assert diff.is_equivalent, diff.report()
    # and the counts are identical, not merely geometrically matched
    assert store.summary() == store2.summary()
