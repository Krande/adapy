"""Flavour detection, and the invariant that importing ada does not drag DEXPI in with it."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ada.cadit.dexpi import DexpiFlavour, sniff_flavour

PROTEUS = """<?xml version="1.0" encoding="utf-8"?>
<PlantModel>
  <PlantInformation SchemaVersion="3.6.1" OriginatingSystem="PID Kit" Units="Millimetre"/>
  <Equipment ID="E1" ComponentClass="Tank"/>
</PlantModel>
"""

DEXPI20 = """<?xml version="1.0" encoding="utf-8"?>
<Model name="example" uri="http://www.example.org">
  <Import prefix="Plant" source="https://data.dexpi.org/models/2.0.0/Plant.xml"/>
  <Object type="Core/EngineeringModel"/>
</Model>
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_sniff_proteus_from_path(tmp_path):
    assert sniff_flavour(_write(tmp_path, "p.xml", PROTEUS)) is DexpiFlavour.PROTEUS


def test_sniff_dexpi20_from_path(tmp_path):
    assert sniff_flavour(_write(tmp_path, "d.xml", DEXPI20)) is DexpiFlavour.DEXPI20


def test_sniff_accepts_a_string_path(tmp_path):
    assert sniff_flavour(str(_write(tmp_path, "p.xml", PROTEUS))) is DexpiFlavour.PROTEUS


def test_sniff_accepts_an_element_and_a_tree():
    root = ET.fromstring(DEXPI20)
    assert sniff_flavour(root) is DexpiFlavour.DEXPI20
    assert sniff_flavour(ET.ElementTree(root)) is DexpiFlavour.DEXPI20


def test_sniff_matches_the_local_name_of_a_namespaced_root():
    root = ET.fromstring('<PlantModel xmlns="http://example.org/proteus"/>')
    assert sniff_flavour(root) is DexpiFlavour.PROTEUS


def test_sniff_names_the_root_it_actually_found(tmp_path):
    path = _write(tmp_path, "other.xml", "<ifcXML><header/></ifcXML>")
    with pytest.raises(ValueError, match="<ifcXML>"):
        sniff_flavour(path)


def test_sniff_reports_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        sniff_flavour(tmp_path / "nope.xml")


def test_sniff_reads_only_the_root_element(tmp_path):
    """A truncated file still sniffs: the root start tag is all that is read."""
    path = _write(tmp_path, "truncated.xml", '<PlantModel><Equipment ID="E1"')
    assert sniff_flavour(path) is DexpiFlavour.PROTEUS


def test_ada_import_does_not_pull_in_dexpi():
    """PR-7 risk item: nothing under cadit/dexpi may load at ``import ada`` time."""
    import subprocess
    import sys

    code = "import sys, ada; print([m for m in sys.modules if m.startswith('ada.cadit.dexpi')])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
