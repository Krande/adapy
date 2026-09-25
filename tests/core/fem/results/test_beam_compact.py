"""The compact beam-solids artefact (AFBS), measured against the mesh it replaces.

The format only earns its place if expanding it gives back exactly what the
bake used to ship. So the claims here are equalities, not resemblances:

* the file round-trips — read(write(x)) is x, field for field;
* expanding a file produced from a list of beams reproduces
  ``tessellate_beams_to_solid_mesh(method="procedural")``: the same triangles,
  the same draw ranges, the same node pairs, positions within float32 and
  ``t`` within float32;
* ``t`` really does vary around a ring on an eccentric beam, which is the
  reason it is recomputed in the browser rather than shipped per section;
* a beam the extruder cannot take is dropped and said so, because the compact
  format has no generator to ship for it;
* the committed frontend fixture still matches what this expander produces.
"""

from __future__ import annotations

import numpy as np
import pytest

from ada import Beam, BeamTapered, Section
from ada.fem.results.artefacts.beam_compact import (
    collect_beam_solid_instances,
    expand_beam_solid_instances,
    read_beam_solids_compact,
    write_beam_solids_compact,
)
from ada.fem.results.artefacts.beam_solids import tessellate_beams_to_solid_mesh
from ada.fem.results.artefacts.formats import (
    BEAM_COMPACT_BEAM_BYTES,
    BEAM_COMPACT_HEADER_BYTES,
    BEAM_COMPACT_MAGIC,
)

# the compact artefact takes its section mesh from adacpp, so without it there is
# nothing here to assert against: every beam falls back to the kernel by design.
# The adacpp CI leg is where these run.


# Marked rather than skipped: deselected where adacpp is absent, which is the env this
# module was never meant to run in.
pytestmark = pytest.mark.adacpp

# Positions are float32 in the file and float32 out of the expander; the
# reference tessellation is float64. "The same vertex" is therefore float32
# epsilon on a coordinate of a few metres, the same bound the extruder's own
# parity tests use against OCC.
F32_TOL = 2e-6


def _named(name: str) -> Section:
    return Section(name, from_str=name)


def _mixed_beams():
    """A deck-shaped little mix: four section kinds, two of them shared, and a
    beam whose two ends carry different offsets so its frame tilts.

    Returns ``(beams, points)`` where ``beams`` is the tuple list the bake
    hands the tessellator and ``points`` is the main mesh's point buffer.
    """

    points = np.array(
        [
            [0.0, 0.0, 0.0],
            [4.0, 0.0, 0.0],
            [0.0, 2.0, 0.0],
            [4.0, 2.0, 0.0],
            [0.0, 4.0, 0.0],
            [4.0, 4.0, 0.5],
            [0.0, 6.0, 0.0],
            [4.0, 6.0, 0.0],
            [0.0, 8.0, 0.0],
            [4.0, 8.0, 0.0],
        ],
        dtype=float,
    )

    hea = _named("HEA300")
    tub = _named("TUB375x35")
    specs = [
        (hea, 0, 1, None, None),
        (_named("IG650x300x25x40"), 2, 3, None, None),
        # Unequal eccentricities: the extrusion axis stops being the element
        # axis, so t varies within a ring.
        (tub, 4, 5, (0.0, 0.05, 0.0), (0.0, -0.05, 0.0)),
        (_named("HP180x10"), 6, 7, None, None),
        # A repeat of the first section — the outline table must not grow.
        (hea, 8, 9, None, None),
    ]

    beams = []
    for i, (sec, a, b, e1, e2) in enumerate(specs):
        bm = Beam(f"b{i}", tuple(points[a]), tuple(points[b]), sec=sec, up=(0, 0, 1))
        if e1 is not None:
            bm.e1 = np.asarray(e1, dtype=float)
        if e2 is not None:
            bm.e2 = np.asarray(e2, dtype=float)
        beams.append((bm, 100 + i, a, b, points[a], points[b]))
    return beams, points


def test_outline_table_holds_one_entry_per_distinct_section():
    beams, _ = _mixed_beams()
    inst = collect_beam_solid_instances(beams, total_beams=len(beams))

    assert inst is not None
    assert inst.n_beams == 5
    # Four distinct sections; the HEA300 shared by beams 0 and 4 is stored once
    # and both reference index 0 — the whole point of the format.
    assert len(inst.sections) == 4
    assert inst.section_idx.tolist() == [0, 1, 2, 3, 0]
    assert inst.n_verts == sum(2 * inst.sections[int(s)].n_points for s in inst.section_idx)


def test_file_round_trips(tmp_path):
    beams, _ = _mixed_beams()
    inst = collect_beam_solid_instances(beams, total_beams=len(beams))

    path = tmp_path / "fea.beam_solids.compact.bin"
    n_beams, n_verts = write_beam_solids_compact(inst, path)
    assert (n_beams, n_verts) == (inst.n_beams, inst.n_verts)

    data = path.read_bytes()
    assert data[:4] == BEAM_COMPACT_MAGIC
    # Size is fully determined by the table plus the fixed-width beam records.
    section_bytes = sum(8 + s.n_points * 2 * 4 + s.n_triangles * 3 * 4 for s in inst.sections)
    assert len(data) == BEAM_COMPACT_HEADER_BYTES + section_bytes + n_beams * BEAM_COMPACT_BEAM_BYTES

    back = read_beam_solids_compact(path)
    assert back.n_beams == inst.n_beams
    assert len(back.sections) == len(inst.sections)
    for got, want in zip(back.sections, inst.sections):
        assert np.array_equal(got.points, want.points)
        assert np.array_equal(got.triangles, want.triangles)
    for field in ("label", "section_idx", "node0", "node1", "origin", "xvec", "yvec", "length"):
        assert np.array_equal(getattr(back, field), getattr(inst, field)), field

    # And writing what was read reproduces the file byte for byte.
    again = tmp_path / "again.bin"
    write_beam_solids_compact(back, again)
    assert again.read_bytes() == data


def test_expansion_reproduces_the_tessellated_mesh(tmp_path):
    """The format's whole contract: what comes out of the expander is the mesh
    the GLB path would have baked."""

    beams, points = _mixed_beams()

    inst = collect_beam_solid_instances(list(beams), total_beams=len(beams))
    path = tmp_path / "compact.bin"
    write_beam_solids_compact(inst, path)
    got = expand_beam_solid_instances(read_beam_solids_compact(path), points)

    want = tessellate_beams_to_solid_mesh(list(beams), method="procedural")
    assert want is not None
    assert want.skip_reasons == {}, "the reference must not have fallen back to OCC here"

    assert got.points.shape == want.points.shape
    assert np.abs(np.asarray(got.points, dtype=np.float64) - want.points).max() < F32_TOL
    assert np.array_equal(got.triangles, want.triangles)
    assert np.array_equal(got.vertex_node0, want.vertex_node0)
    assert np.array_equal(got.vertex_node1, want.vertex_node1)
    assert np.abs(got.vertex_t - want.vertex_t).max() < 1e-6
    assert [(r.label, r.tri_start, r.tri_count) for r in got.element_ranges] == [
        (r.label, r.tri_start, r.tri_count) for r in want.element_ranges
    ]


def test_t_varies_within_a_ring_on_an_eccentric_beam():
    """Why ``t`` is recomputed per vertex instead of shipped per section.

    With different offsets at the two ends the extrusion axis tilts away from
    the element axis, so a ring of vertices -- all at the same axial station of
    the EXTRUSION -- projects onto a spread of stations of the ELEMENT.
    """

    points = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]], dtype=float)
    bm = Beam("ecc", (0, 0, 0), (4, 0, 0), sec=_named("TUB375x35"), up=(0, 0, 1))
    bm.e1 = np.array([0.0, 0.05, 0.0])
    bm.e2 = np.array([0.0, -0.05, 0.0])

    inst = collect_beam_solid_instances([(bm, 7, 0, 1, points[0], points[1])], total_beams=1)
    got = expand_beam_solid_instances(inst, points)

    n = inst.sections[0].n_points
    near = got.vertex_t[:n]
    assert float(near.max() - near.min()) > 1e-5, "t must not be constant around an eccentric ring"

    # A beam with no eccentricity, by contrast, has a flat ring — this is the
    # case the "one t per ring" shortcut would have been correct for.
    straight = Beam("straight", (0, 0, 0), (4, 0, 0), sec=_named("TUB375x35"), up=(0, 0, 1))
    inst2 = collect_beam_solid_instances([(straight, 8, 0, 1, points[0], points[1])], total_beams=1)
    got2 = expand_beam_solid_instances(inst2, points)
    n2 = inst2.sections[0].n_points
    flat = got2.vertex_t[:n2]
    assert float(flat.max() - flat.min()) == 0.0


def test_unsupported_beams_are_dropped_and_counted():
    """A tapered beam has no generator to ship, so it is not in the artefact --
    and the manifest has to say so, since the viewer will draw it as a line."""

    section = _named("HEA300")
    points = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=float)
    beams = [
        (Beam("ok", (0, 0, 0), (3, 0, 0), sec=section), 1, 0, 1, points[0], points[1]),
        (
            BeamTapered("tap", (0, 0, 0), (3, 0, 0), sec=section, tap=_named("HEA200")),
            2,
            0,
            1,
            points[0],
            points[1],
        ),
    ]

    inst = collect_beam_solid_instances(beams, total_beams=len(beams))
    assert inst is not None
    assert inst.n_beams == 1
    assert inst.label.tolist() == [1]
    assert inst.skip_reasons == {"compact-unsupported[tapered]": 1}
    assert inst.total_beams == 2


def test_no_prismatic_beams_yields_nothing():
    """Same contract as the tessellator: nothing to draw means no artefact,
    not an empty one."""

    section = _named("HEA300")
    points = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=float)
    tapered = BeamTapered("tap", (0, 0, 0), (3, 0, 0), sec=section, tap=_named("HEA200"))
    assert collect_beam_solid_instances([(tapered, 1, 0, 1, points[0], points[1])], total_beams=1) is None


def test_zero_length_element_axis_collapses_t_to_zero():
    """A beam whose two NODES coincide has no axis to project onto; every
    vertex takes t=0 so the warp lerp collapses onto disp[node0]."""

    points = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=float)
    bm = Beam("b", (0, 0, 0), (3, 0, 0), sec=_named("HEA300"), up=(0, 0, 1))
    inst = collect_beam_solid_instances([(bm, 1, 0, 1, points[0], points[1])], total_beams=1)
    got = expand_beam_solid_instances(inst, points)
    assert not got.vertex_t.any()


def test_reader_rejects_a_foreign_or_truncated_file(tmp_path):
    beams, _ = _mixed_beams()
    inst = collect_beam_solid_instances(beams, total_beams=len(beams))
    path = tmp_path / "compact.bin"
    write_beam_solids_compact(inst, path)
    good = path.read_bytes()

    bad_magic = tmp_path / "bad_magic.bin"
    bad_magic.write_bytes(b"XXXX" + good[4:])
    with pytest.raises(ValueError, match="bad magic"):
        read_beam_solids_compact(bad_magic)

    bad_version = tmp_path / "bad_version.bin"
    bad_version.write_bytes(good[:4] + (99).to_bytes(4, "little") + good[8:])
    with pytest.raises(ValueError, match="version 99"):
        read_beam_solids_compact(bad_version)

    truncated = tmp_path / "truncated.bin"
    truncated.write_bytes(good[: len(good) - BEAM_COMPACT_BEAM_BYTES // 2])
    with pytest.raises(ValueError, match="truncated"):
        read_beam_solids_compact(truncated)


def test_committed_frontend_fixture_is_current():
    """The TypeScript expander is tested against a fixture this expander
    produced. Regenerate it (``python tools/gen_beam_solids_compact_fixture.py``)
    whenever the format or the arithmetic changes, or the two implementations
    are being compared against a stale answer."""

    import importlib.util
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[4]
    script = root / "tools" / "gen_beam_solids_compact_fixture.py"
    spec = importlib.util.spec_from_file_location("gen_beam_solids_compact_fixture", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    blob, payload = mod.build_fixture()
    out_dir = root / mod.FIXTURE_DIR
    assert (out_dir / mod.BIN_NAME).read_bytes() == blob, "committed AFBS fixture is stale"
    assert (out_dir / mod.JSON_NAME).read_bytes() == mod.payload_bytes(
        payload
    ), "committed expected-arrays fixture is stale"
