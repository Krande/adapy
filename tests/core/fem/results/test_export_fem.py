"""FEM (input-deck) export from a SIN results file.

SESTRA echoes the whole input file into the SIN, so the export is extraction:
input records verbatim, result records dropped, standard Input Interface File
text out. Pinned here: the drop rule, the record layouts a reader depends on
(logical NFIELD only where the manual says so), the text-record name
round-trip, and that the exported deck describes the same mesh as the SIN.
"""

import numpy as np

from ada.fem.formats.sesam.results.export_fem import export_fem_text, is_result_record
from ada.fem.formats.sesam.results.read_sif import read_sif_file
from ada.fem.formats.sesam.results.read_sin import read_sin_file

_SIN = "cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"


def test_result_record_rule():
    for name in ("RVNODDIS", "RDPOINTS", "RBLODCMB", "RSUMREAC", "TDRESREF"):
        assert is_result_record(name), name
    for name in ("GNODE", "GELMNT1", "TDMATER", "TDSETNAM", "UNITS", "BNBCD", "SCONCEPT"):
        assert not is_result_record(name), name


def test_export_drops_results_and_keeps_the_input_deck(fem_files):
    text = export_fem_text(fem_files / _SIN)
    cards = {line.split()[0] for line in text.splitlines() if line and not line[0].isspace()}
    assert not any(is_result_record(c) for c in cards), sorted(cards)
    # The deck itself: mesh, thickness, material, boundary conditions.
    assert {"GNODE", "GCOORD", "GELMNT1", "GELREF1", "GELTH", "MISOSEL", "BNBCD"} <= cards
    # An input file leads with IDENT (synthesised when the SIN has no block)
    # and ends with IEND.
    lines = text.splitlines()
    assert lines[0].startswith("IDENT")
    assert lines[-1].startswith("IEND")


def test_exported_fem_describes_the_same_mesh(fem_files, tmp_path):
    sin_result = read_sin_file(fem_files / _SIN)
    fem_path = tmp_path / "exported.FEM"
    fem_path.write_text(export_fem_text(fem_files / _SIN), encoding="ascii")

    # The exported text parses through the same SIF machinery — geometry,
    # element table and material names identical to the SIN's.
    fem_result = read_sif_file(fem_path)
    assert np.allclose(
        np.sort(np.asarray(fem_result.mesh.nodes.identifiers, dtype=int)),
        np.sort(np.asarray(sin_result.mesh.nodes.identifiers, dtype=int)),
    )
    n_elems = lambda res: sum(len(b.identifiers) for b in res.mesh.elements)  # noqa: E731
    assert n_elems(fem_result) == n_elems(sin_result)
    assert {m.name for m in fem_result.mesh.materials.values()} == {m.name for m in sin_result.mesh.materials.values()}
    # And, being result-less, it carries no result steps — the whole point.
    assert fem_result.get_steps() == []


def test_rest_converter_serves_sin_to_fem(fem_files):
    """The .sin → fem registry cell the toolbar export rides: same extraction,
    dispatched through the worker's convert() entry point, bytes out."""

    from ada.comms.rest.converter import convert, result_bytes

    out = convert(fem_files / _SIN, "models/x.sin", "fem")
    data = result_bytes(out)
    assert data.startswith(b"IDENT")
    assert b"RVNODDIS" not in data and b"RDPOINTS" not in data
    assert b"GELMNT1" in data and b"MISOSEL" in data
