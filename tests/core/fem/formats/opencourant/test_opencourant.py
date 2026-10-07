"""OpenCourant format: animation parser, FEAResult conversion, .radanim container, deck writer."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from ada.fem.formats.opencourant.results.container import (
    iter_run_frames,
    pack_radanim,
    read_opencourant_results,
)
from ada.fem.formats.opencourant.results.read_anim import (
    FASTMAGI10,
    AnimFormatError,
    read_anim_bytes,
)
from ada.fem.results.field_data import ElementFieldData, NodalFieldData, NodalFieldType
from ada.fem.shapes.definitions import ShellShapes

COORDS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0]], dtype=np.float32)
# one quad and one triangle (third node repeated), solver numbering
CONN = np.array([[0, 1, 2, 3], [1, 4, 2, 2]])
NODE_IDS = [11, 12, 13, 14, 15]
ELEM_IDS = [101, 102]


def _text(s: str, width: int) -> bytes:
    return s.encode("latin-1").ljust(width, b"\0")


def encode_anim(time: float, disp: np.ndarray, vonm: np.ndarray) -> bytes:
    """Minimal big-endian FASTMAGI10 state: 2D block, 1 element scalar, 1 vector, numbering on."""
    n_nodes, n_facets = len(COORDS), len(CONN)
    b = bytearray()
    b += struct.pack(">i", FASTMAGI10)
    b += struct.pack(">f", time)
    b += _text("Time=", 81) + _text("anim", 81) + _text("run", 81)
    b += struct.pack(">10i", 0, 1, 0, 0, 0, 0, 0, 0, 0, 0)  # numbering only
    b += struct.pack(">8i", n_nodes, n_facets, 1, 0, 1, 1, 0, 0)
    b += (COORDS + disp).astype(">f4").tobytes()
    b += CONN.astype(">i4").tobytes()
    b += bytes(n_facets)  # nothing deleted
    b += struct.pack(">i", n_facets) + _text("1:plate", 50)
    b += np.zeros(3 * n_nodes, dtype=">u2").tobytes()  # packed normals
    b += _text("Von Mises", 81)
    b += np.asarray(vonm, dtype=">f4").tobytes()
    b += _text("Displacement", 81)
    b += np.asarray(disp, dtype=">f4").tobytes()
    b += np.asarray(NODE_IDS, dtype=">i4").tobytes()
    b += np.asarray(ELEM_IDS, dtype=">i4").tobytes()
    return bytes(b)


def _disp(scale: float) -> np.ndarray:
    d = np.zeros_like(COORDS)
    d[:, 2] = -scale * np.arange(len(COORDS))
    return d


def test_read_anim_state():
    frame = read_anim_bytes(encode_anim(0.5, _disp(0.1), [1.0e6, 2.0e6]))
    assert frame.time == pytest.approx(0.5)
    assert frame.num_nodes == 5
    assert frame.node_ids.tolist() == NODE_IDS
    assert frame.shells.connectivity.tolist() == CONN.tolist()
    assert frame.shells.element_ids.tolist() == ELEM_IDS
    assert frame.shells.part_names == ["1:plate"]
    np.testing.assert_allclose(frame.shells.scalars["Von Mises"], [1.0e6, 2.0e6])
    np.testing.assert_allclose(frame.vectors["Displacement"], _disp(0.1), atol=1e-7)


def test_read_anim_rejects_other_magic():
    with pytest.raises(AnimFormatError):
        read_anim_bytes(struct.pack(">i", 1234) + bytes(64))


def _write_run(tmp_path, root="run"):
    for i, t in enumerate([0.0, 0.01, 0.02], start=1):
        (tmp_path / f"{root}A{i:03d}").write_bytes(encode_anim(t, _disp(0.1 * i - 0.1), [i * 1e6, i * 2e6]))
    return tmp_path


def test_fea_result_from_run_dir(tmp_path):
    res = read_opencourant_results(_write_run(tmp_path), root="run")

    assert res.mesh.nodes.identifiers.tolist() == NODE_IDS
    shapes = {b.elem_info.type: b for b in res.mesh.elements}
    assert set(shapes) == {ShellShapes.QUAD, ShellShapes.TRI}
    assert shapes[ShellShapes.TRI].node_refs.tolist() == [[12, 15, 13]]
    assert shapes[ShellShapes.QUAD].identifiers.tolist() == [101]

    disp = sorted((r for r in res.results if isinstance(r, NodalFieldData) and r.name == "U"), key=lambda r: r.step)
    assert [r.step for r in disp] == pytest.approx([0.0, 0.01, 0.02])
    assert disp[0].field_type == NodalFieldType.DISP
    np.testing.assert_allclose(disp[-1].values[:, 3], _disp(0.2)[:, 2], atol=1e-7)

    vonm = [r for r in res.results if isinstance(r, ElementFieldData) and r.name == "Von_Mises"]
    assert {r.elem_type for r in vonm} == {ShellShapes.QUAD, ShellShapes.TRI}
    last_tri = max((r for r in vonm if r.elem_type == ShellShapes.TRI), key=lambda r: r.step)
    assert last_tri.values.tolist() == [[102, 1, 6e6]]


def test_radanim_roundtrip_and_stream_specs(tmp_path):
    from ada.fem.results.artefacts.readers import make_stream_reader

    radanim = pack_radanim(_write_run(tmp_path), "run")
    assert radanim.name == "run.radanim"
    assert [f.time for f in iter_run_frames(radanim)] == pytest.approx([0.0, 0.01, 0.02])

    reader = make_stream_reader(radanim)
    specs = {s.name: s for s in reader.field_specs()}
    assert specs["U"].category == "displacement"
    assert specs["U"].n_steps == 3
    # A time history: the viewer opens on the last frame and plays through time.
    assert {s.analysis_kind for s in reader.field_specs()} == {"transient"}
    assert {s.analysis_kind for s in reader.element_field_specs()} == {"transient"}
    elem_specs = {(s.name, s.elem_type) for s in reader.element_field_specs()}
    assert ("Von_Mises", "triangle") in {(n, t.lower()) for n, t in elem_specs}


def test_writer_writes_starter_and_engine(tmp_path):
    pytest.importorskip("gmsh")
    import ada
    from ada.fem import (
        Bc,
        FemSet,
        Interaction,
        InteractionProperty,
        PredefinedField,
        Surface,
    )
    from ada.fem.interactions import ContactTypes
    from ada.fem.meshing import mesh_shell_bodies
    from ada.fem.steps import StepExplicit

    floor = ada.Plate.from_3d_points("floor", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.01)
    lid = ada.Plate.from_3d_points("lid", [(0, 0, 0.1), (1, 0, 0.1), (1, 1, 0.1), (0, 1, 0.1)], 0.01)
    fem = mesh_shell_bodies([[floor], [lid]], 0.25, name="pair")
    part = ada.Part("Pair")
    part.fem = fem
    a = ada.Assembly("A") / part

    lid_els = [el for el in fem.elements if el.refs[0] is lid]
    floor_els = [el for el in fem.elements if el.refs[0] is floor]
    lid_set = fem.add_set(FemSet("lid_els", lid_els, FemSet.TYPES.ELSET))
    floor_set = fem.add_set(FemSet("floor_els", floor_els, FemSet.TYPES.ELSET))
    lid_nodes = fem.add_set(FemSet("lid_nodes", list({n for el in lid_els for n in el.nodes}), FemSet.TYPES.NSET))
    fem.add_bc(Bc("fix", floor_set, [1, 2, 3, 4, 5, 6]))
    fem.add_predefined_field(
        PredefinedField("v0", PredefinedField.TYPES.VELOCITY, lid_nodes, dofs=[3], magnitude=[-2.0])
    )
    s1 = fem.add_surface(Surface("floor", Surface.TYPES.ELEMENT, floor_set))
    s2 = fem.add_surface(Surface("lid", Surface.TYPES.ELEMENT, lid_set))
    fem.add_interaction(Interaction("c", ContactTypes.SURFACE, s1, s2, InteractionProperty("p", friction=0.1)))
    a.fem.add_step(StepExplicit("drop", total_time=0.01, output_interval=0.002))

    from ada.fem.formats.opencourant.write.writer import to_fem

    to_fem(a, "pair", tmp_path)
    starter = (tmp_path / "pair_0000.rad").read_text()
    engine = (tmp_path / "pair_0001.rad").read_text()

    for kw in ("/BEGIN", "/NODE", "/PROP/SHELL/", "/PART/", "/BCS/", "/INIVEL/TRA/", "/SURF/SEG/", "/END"):
        assert kw in starter, kw
    assert starter.count("/INTER/TYPE7/") == 2  # symmetric contact = both directions
    assert "/SH3N/" in starter or "/SHELL/" in starter
    assert "/MAT/PLAS_JOHNS/" in starter  # S420 has a yield stress -> Johnson-Cook
    assert "/RUN/pair/1" in engine and "/ANIM/VECT/DISP" in engine
    # every card line stays within the 100-column Radioss record
    assert max(len(line) for line in starter.splitlines()) <= 100
