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


# ---- explicit-step loads ----------------------------------------------------------------------------------------


def _pressurised_box(tmp_path, load_type: str):
    import ada
    from ada.fem import Surface
    from ada.fem.common import Amplitude
    from ada.fem.loads import LoadGravity, LoadPoint, LoadPressure
    from ada.fem.steps import StepExplicit

    from ...theory_models import closed_box

    a, p, fs = closed_box(theory="shell")
    step = a.fem.add_step(StepExplicit("ramp", total_time=1.0, mass_damping=50.0))
    inner = p.fem.add_surface(Surface("inner", Surface.TYPES.ELEMENT, fs, el_face_index=-1))
    if load_type == "pressure":
        step.add_load(LoadPressure("p_in", 400.0, inner, amplitude=Amplitude("ramp", [0.0, 0.5, 1.0], [0.0, 1.0, 1.0])))
        step.add_load(LoadGravity("g", -9.81))
    else:
        nset = p.fem.add_set(ada.fem.FemSet("corner", [p.fem.nodes.from_id(1)], "nset"))
        step.add_load(LoadPoint("f", 10.0, nset, 3))
    from ada.fem.formats.opencourant.write.writer import to_fem

    to_fem(a, "box", tmp_path)
    return (tmp_path / "box_0000.rad").read_text()


def test_writer_writes_pressure_gravity_amplitude_and_damping(tmp_path):
    deck = _pressurised_box(tmp_path, "pressure")
    # amplitude -> /FUNCT points; a load without one gets a constant curve
    ramp = [r for r in deck.split("/FUNCT/")[1].splitlines()[1:] if not r.startswith("#")]
    assert ramp[0] == "ramp"
    assert [[float(r[i : i + 20]) for i in (0, 20)] for r in ramp[1:4]] == [[0, 0], [0.5, 1], [1, 1]]
    pload = [r for r in deck.split("/PLOAD/")[1].splitlines() if not r.startswith("#")][2]
    surf_id, funct_id = int(pload[:10]), int(pload[10:20])
    assert funct_id == 1 and float(pload[60:80]) == 0 and float(pload[80:100]) == pytest.approx(400.0)
    # a positive /PLOAD pushes along the segment normal: the inner (negative) face is wound as the element runs,
    # so the inner pressure pushes the walls outward
    seg = deck.split(f"/SURF/SEG/{surf_id}\n")[1].splitlines()
    assert seg[0] == "inner"
    assert [int(seg[1][i : i + 10]) for i in range(10, 50, 10)] == [1, 4, 3, 2]  # element 1 is 1-4-3-2
    grav = [r for r in deck.split("/GRAV/")[1].splitlines() if not r.startswith("#")][2]
    assert grav[10:20].strip() == "Z" and float(grav[80:100]) == pytest.approx(-9.81)
    assert int(grav[:10]) == 2  # the constant curve
    damp = [r for r in deck.split("/DAMP/")[1].splitlines() if not r.startswith("#")][2]
    assert float(damp[:20]) == pytest.approx(50.0) and float(damp[20:40]) == 0.0
    assert max(len(line) for line in deck.splitlines()) <= 100


def test_writer_refuses_a_load_it_cannot_write(tmp_path):
    with pytest.raises(NotImplementedError):
        _pressurised_box(tmp_path, "point")


def test_engine_deck_asks_for_membrane_stress_tensors(tmp_path):
    from ada.fem.formats.opencourant.write.writer import engine_deck_str
    from ada.fem.steps import StepExplicit

    deck = engine_deck_str("run", StepExplicit("s", total_time=1.0))
    assert "/ANIM/SHELL/TENS/STRESS/MEMB" in deck


# ---- time histories ---------------------------------------------------------------------------------------------


def _rec(payload: bytes) -> bytes:
    n = struct.pack(">i", len(payload))
    return n + payload + n


def encode_th(times, cavity_rows) -> bytes:
    """A minimal format-3040 ``T01``: one part, one material, one property, one subset, one /TH/MONV group with one
    object and the seven MONV variables, three global variables."""
    t40 = lambda s: _text(s, 40)  # noqa: E731
    b = _rec(struct.pack(">i", 3040) + b" " * 80)
    b += _rec(b"date and code version".ljust(80))
    b += _rec(struct.pack(">6i", 1, 1, 1, 1, 1, 3))  # parts, mats, props, subsets, TH groups, globals
    b += _rec(struct.pack(">3i", 1, 2, 6))
    b += _rec(struct.pack(">i", 1) + t40("skin") + struct.pack(">4i", 0, 1, 1, 0))  # part without variables
    b += _rec(struct.pack(">i", 1) + t40("fabric"))
    b += _rec(struct.pack(">i", 1) + t40("shell"))
    b += _rec(struct.pack(">5i", 0, 0, 0, 1, 0) + t40("GLOBAL MODEL"))
    b += _rec(struct.pack(">i", 1))
    b += _rec(struct.pack(">5i", 1, 107, 0, 1, 7) + t40("cavities"))
    b += _rec(struct.pack(">i", 1) + t40("cushion"))
    b += _rec(struct.pack(">7i", 1, 2, 3, 4, 5, 6, 7))
    for t, row in zip(times, cavity_rows):
        b += _rec(struct.pack(">f", t))
        b += _rec(struct.pack(">3f", 1.0, 2.0, 3.0))
        b += _rec(np.asarray(row, dtype=">f4").tobytes())
    return b


def test_read_time_history():
    from ada.fem.formats.opencourant.results.read_th import (
        MONV_VARIABLE_NAMES,
        THFormatError,
        read_th_bytes,
    )

    rows = [[1.2, 10.0, 101_725.0, 22.0, 293.0, 0.0, 0.0], [1.1, 9.5, 101_500.0, 21.5, 290.0, 0.5, 20.0]]
    data = encode_th([0.0, 0.1], rows)
    th = read_th_bytes(data + _rec(struct.pack(">f", 0.2))[:-6])  # a partial last state is dropped
    np.testing.assert_allclose(th.times, [0.0, 0.1], atol=1e-7)
    assert th.global_names == ["INTERNAL ENERGY", "KINETIC ENERGY", "X-MOMENTUM"]
    np.testing.assert_allclose(th.global_series("KINETIC ENERGY"), [2.0, 2.0])
    g = th.group("cavities")
    assert g.type_code == 107 and g.object_ids == [1] and g.object_titles == ["cushion"]
    assert g.variable_names(MONV_VARIABLE_NAMES) == ["MASS", "VOL", "P", "A", "T", "AO", "UO"]
    assert g.values.shape == (2, 1, 7)
    np.testing.assert_allclose(g.series(3), [101_725.0, 101_500.0])
    with pytest.raises(THFormatError):
        read_th_bytes(_rec(struct.pack(">i", 7)))
