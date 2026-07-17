"""Stage 5: the conformance harness (oracle) + the store-export faithfulness guarantee."""

import tempfile
from pathlib import Path

import ada
from ada.cadit.sat.read.to_brep import genie_xml_to_brep, sat_store_to_brep
from ada.cadit.sat.store import SatReaderFactory
from ada.cadit.sat.write.writer import part_to_sat_writer
from ada.geom.brep.conformance import conformance
from ada.geom.brep.diff import store_equivalence


def _xml(fem_files):
    return (fem_files / "sesam/curved_plates.xml").resolve().absolute()


def test_conformance_harness_runs_and_classifies(fem_files):
    """The weld-vs-truth harness returns a classified diff (Class 1/2/3)."""
    d = conformance(_xml(fem_files))
    cls = d.classify()
    assert set(cls) == {"class1_weld", "class2_split_imprint", "class3_non_derivable"}
    assert all(v >= 0 for v in cls.values())


def test_store_export_is_faithful(fem_files):
    """The 4c store-based export re-imports equivalent to the import truth — i.e.
    routing part_to_sat_writer through the store reproduces the source topology
    (the guarantee the weld does NOT yet meet)."""
    xml = _xml(fem_files)
    truth = genie_xml_to_brep(xml)

    a = ada.from_genie_xml(str(xml), build_topology_store=True)
    sw = part_to_sat_writer(a)  # store attached → store path
    with tempfile.TemporaryDirectory() as td:
        sat_path = Path(td) / "store_export.sat"
        sat_path.write_text(sw.to_str())
        f = SatReaderFactory(sat_path)
        f.load_sat_data_from_file()
        exported = sat_store_to_brep(f.sat_store)

    d = store_equivalence(truth, exported)
    assert d.is_equivalent, d.report()


def test_store_export_references_all_beams(fem_files):
    """Every beam resolves to a named store edge via the export's edge_map."""
    xml = _xml(fem_files)
    a = ada.from_genie_xml(str(xml), build_topology_store=True)
    from ada import Beam
    from ada.api.beams import BeamRevolve

    beams = list(a.get_all_physical_objects(by_type=(Beam, BeamRevolve)))
    if not beams:
        return  # fixture has no beams; nothing to assert
    sw = part_to_sat_writer(a)
    referenced = sum(1 for b in beams if sw.edge_map.get(b.guid))
    assert referenced == len(beams), f"{len(beams) - referenced} beams unreferenced"
