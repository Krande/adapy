import sys

import ada
from ada.cadit.ifc.write import native_ifc_writer


def test_the_native_writer_does_not_need_ifcopenshell_validate(monkeypatch, tmp_path):
    written = []
    monkeypatch.setattr(native_ifc_writer, "native_ifc_writer_available", lambda: True)
    monkeypatch.setattr(native_ifc_writer, "native_write_ifc", lambda assembly, path: written.append(path))
    monkeypatch.setitem(sys.modules, "ifcopenshell.validate", None)  # makes importing it raise ImportError

    destination = tmp_path / "out.ifc"
    assert ada.Assembly("a").to_ifc(destination, writer="native") is None
    assert written == [destination.resolve()]
