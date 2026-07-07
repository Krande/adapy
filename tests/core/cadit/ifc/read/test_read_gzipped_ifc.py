"""Transparently open a gzip-compressed IFC shipped under a plain .ifc name.

Some exporters/servers gzip the model but keep the .ifc extension; ifcopenshell.open then fails
with 'Unable to parse IFC SPF header'. The reader detects the gzip magic (0x1f 0x8b) and inflates
the SPF text before parsing, so from_ifc reads it like any other model.
"""

from __future__ import annotations

import gzip

import ifcopenshell

import ada
from ada.config import Config


def test_from_ifc_opens_gzip_compressed_ifc(example_files):
    src = example_files / "ifc_files/beam-standard-case-gz.ifc"
    # sanity: the fixture really is gzip and ifcopenshell.open can't read it directly
    assert src.read_bytes()[:2] == b"\x1f\x8b"

    Config().update_config_globally("ifc_import_shape_geom", True)
    a = ada.from_ifc(src)
    objs = list(a.get_all_physical_objects())
    assert len(objs) == 18  # 18 IfcBeam in the buildingSMART beam-standard-case sample


def test_open_ifc_file_helper_handles_gzip_and_plain(example_files, tmp_path):
    from ada.cadit.ifc.store import open_ifc_file

    gz = example_files / "ifc_files/beam-standard-case-gz.ifc"
    f = open_ifc_file(gz)
    assert f.schema == "IFC4" and len(f.by_type("IfcBeam")) == 18

    # a plain (already-inflated) copy still opens the normal way
    plain = tmp_path / "plain.ifc"
    plain.write_text(gzip.decompress(gz.read_bytes()).decode("utf-8", "replace"))
    assert isinstance(open_ifc_file(plain), ifcopenshell.file)
