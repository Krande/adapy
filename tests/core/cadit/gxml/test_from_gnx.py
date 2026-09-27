"""``ada.from_gnx`` -- the reader that mirrors :meth:`ada.Assembly.to_gnx`.

Reading a workspace already worked through ``from_genie_xml``, which sniffs the ``.gnx``
extension. That is the right thing for a path that arrives from a CLI or a registry, and
the wrong thing for someone reading the API: ``to_gnx`` has no counterpart to look for, so
the workspace reader is findable only by knowing that the *XML* reader takes a zip. Hence a
named entry point with a signature you can read, which these tests pin.

The other thing pinned here is the temporary directory. A workspace is unpacked into one so
the ACIS body can be embedded back into an XML the concept reader understands, and the SAT
that ``build_topology_store`` reads lives *only* there. Building the store after the
directory is gone leaves it silently empty -- an assembly that looks read but re-welds its
plate outlines on the next export.
"""

from __future__ import annotations

import inspect
import pathlib
import tempfile

import pytest

import ada


def _build() -> ada.Assembly:
    """Beams and a plate: the two concept kinds a workspace carries, in one small model.

    ``bm2`` is an asymmetric HP profile on purpose -- a doubly symmetric section round-trips
    even when half its dimensions are dropped.
    """
    p = ada.Part("P") / (
        ada.Beam("bm1", (0, 0, 0), (4, 0, 0), "IPE300"),
        ada.Beam("bm2", (4, 0, 0), (4, 0, 3), "HP200x10"),
        ada.Plate("pl1", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.02),
    )
    return ada.Assembly("rt") / p


def _beams(a: ada.Assembly) -> dict[str, ada.Beam]:
    return {bm.name: bm for bm in a.get_all_physical_objects(by_type=ada.Beam)}


def _plates(a: ada.Assembly) -> dict[str, ada.Plate]:
    return {pl.name: pl for pl in a.get_all_physical_objects(by_type=ada.Plate)}


def _sections(a: ada.Assembly) -> dict[str, ada.Section]:
    return {sec.name: sec for p in a.get_all_parts_in_assembly(True) for sec in p.sections}


def _materials(a: ada.Assembly) -> dict[str, ada.Material]:
    return {mat.name: mat for p in a.get_all_parts_in_assembly(True) for mat in p.materials}


def _mat_values(mat: ada.Material) -> tuple[float, ...]:
    m = mat.model
    return (m.E, m.rho, m.v, m.sig_y)


# -- the entry point itself -------------------------------------------------------------


def test_from_gnx_exists_beside_the_other_readers():
    """``ada.from_gnx`` is the name ``to_gnx`` sends you looking for."""
    assert hasattr(ada, "from_gnx")
    assert "from_gnx" in ada.__all__


def test_from_gnx_takes_an_explicit_signature():
    """Not ``**kwargs``: the point of the function is that its options are discoverable, and
    they must be the same options ``from_genie_xml`` takes, under the same names."""
    sig = inspect.signature(ada.from_gnx)
    assert not [p for p in sig.parameters.values() if p.kind is p.VAR_KEYWORD], sig
    assert not [p for p in sig.parameters.values() if p.kind is p.VAR_POSITIONAL], sig

    xml_sig = inspect.signature(ada.from_genie_xml)
    # The first parameter is the path and is named for the format it reads; the rest are shared.
    assert list(sig.parameters)[1:] == list(xml_sig.parameters)[1:]
    for name in list(sig.parameters)[1:]:
        assert sig.parameters[name].default == xml_sig.parameters[name].default, name
    assert sig.return_annotation == xml_sig.return_annotation


# -- the round trip ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def round_tripped(tmp_path_factory) -> tuple[ada.Assembly, ada.Assembly, ada.Assembly]:
    """``_build()``, the same model read back from a workspace, and from a plain concept XML.

    The third one is the reference the workspace is held against. Two losses in this round
    trip belong to the *reader*, not to the container, and both were measured on the ``.xml``
    path as well (see the tests below): the plate outline comes back rotated by one vertex,
    and an HP section loses ``w_top``/``t_ftop``. Comparing the workspace against the XML
    pins the invariant that actually belongs to ``.gnx`` -- the zip carries everything the
    text carries -- while the comparisons against the source say what the reader does.
    """
    tmp = tmp_path_factory.mktemp("from_gnx_rt")
    src = _build()
    src.to_gnx(tmp / "rt.gnx")
    src.to_genie_xml(tmp / "rt.xml")
    return src, ada.from_gnx(tmp / "rt.gnx"), ada.from_genie_xml(tmp / "rt.xml")


def _cycles(points) -> list[tuple[tuple[float, ...], ...]]:
    """Every rotation of a closed outline, so two outlines can be compared as loops.

    The GeniE round trip gives the outline back rotated: a plate entered
    ``(0,0,0) (0,3,0) (4,3,0) (4,0,0)`` reads back starting at ``(4,0,0)``, because the
    SAT face's loop has its own first coedge. Same loop, same direction, different entry
    point -- so rotation is the equivalence, and a *reversal* (which would flip the plate
    normal) is deliberately still a difference.
    """
    rounded = [tuple(round(float(v), 9) for v in p) for p in points]
    return [tuple(rounded[i:] + rounded[:i]) for i in range(len(rounded))]


def test_round_trip_keeps_every_beam_where_it_was(round_tripped):
    src, gnx, _ = round_tripped
    a, b = _beams(src), _beams(gnx)
    assert sorted(b) == sorted(a)
    for name, bm in sorted(a.items()):
        assert b[name].n1.p == pytest.approx(bm.n1.p), name
        assert b[name].n2.p == pytest.approx(bm.n2.p), name


def test_round_trip_keeps_every_plate(round_tripped):
    src, gnx, _ = round_tripped
    a, b = _plates(src), _plates(gnx)
    assert sorted(b) == sorted(a)
    for name, pl in sorted(a.items()):
        assert b[name].t == pytest.approx(pl.t), name
        assert b[name].material.name == pl.material.name, name
        assert _cycles(b[name].poly.points3d)[0] in _cycles(pl.poly.points3d), (
            f"{name}: the outline is not the same loop.\n"
            f"  read:   {_cycles(b[name].poly.points3d)[0]}\n"
            f"  source: {_cycles(pl.poly.points3d)[0]}"
        )


def test_round_trip_keeps_sections_by_name_and_by_value(round_tripped):
    """``Section.__eq__`` is guid identity, so the comparison that means anything across a
    file boundary is ``unique_props`` -- type and every dimension.

    One difference is expected and is the reader's, measured identically on the ``.xml``
    path: an HP (angular) section comes back with ``w_top``/``t_ftop`` unset, so
    ``HP200x10`` reads as ``(HP, h=0.2, w_top=None, w_btn=0.038, t_w=0.01, t_ftop=None,
    t_fbtn=0.0224)`` against a source that has ``w_top=0.038, t_ftop=0.0224``. It is named
    here rather than tolerated by a looser comparison, so that it reading back *correctly*
    fails this test too and the note gets removed.
    """
    src, gnx, _ = round_tripped
    a, b = _sections(src), _sections(gnx)
    assert sorted(b) == sorted(a)

    props = ["type", "h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt"]
    differs = {
        name: [p for p in props if getattr(b[name], p) != getattr(a[name], p)] for name in sorted(a) if name in b
    }
    assert differs == {"IPE300": [], "HP200x10": ["w_top", "t_ftop"]}, differs
    assert (b["HP200x10"].w_top, b["HP200x10"].t_ftop) == (None, None)


def test_round_trip_keeps_materials_by_name_and_by_value(round_tripped):
    src, gnx, _ = round_tripped
    a, b = _materials(src), _materials(gnx)
    assert sorted(b) == sorted(a)
    for name, mat in sorted(a.items()):
        assert _mat_values(b[name]) == pytest.approx(_mat_values(mat)), name


def test_beams_keep_their_section_and_material(round_tripped):
    """Names surviving in the tables is not the same as the members still pointing at them."""
    src, gnx, _ = round_tripped
    a, b = _beams(src), _beams(gnx)
    for name, bm in sorted(a.items()):
        assert b[name].section.name == bm.section.name, name
        assert b[name].material.name == bm.material.name, name


def test_a_workspace_carries_exactly_what_the_concept_xml_carries(round_tripped):
    """The invariant that belongs to the container rather than to the reader.

    A ``.gnx`` is the concept XML zipped with its ACIS body in a separate member, so reading
    one must land on the same model as reading the equivalent XML -- every beam end, every
    section dimension, every plate outline vertex in the same order. This is what would
    catch the body being lost, truncated or re-welded on the way through the zip, which no
    comparison against the source can distinguish from the reader's own losses.
    """
    _, gnx, xml = round_tripped

    g, x = _beams(gnx), _beams(xml)
    assert sorted(g) == sorted(x)
    for name in sorted(x):
        assert g[name].n1.p == pytest.approx(x[name].n1.p), name
        assert g[name].n2.p == pytest.approx(x[name].n2.p), name
        assert g[name].section.name == x[name].section.name, name

    gp, xp = _plates(gnx), _plates(xml)
    assert sorted(gp) == sorted(xp)
    for name in sorted(xp):
        assert gp[name].t == pytest.approx(xp[name].t), name
        assert _cycles(gp[name].poly.points3d)[0] == _cycles(xp[name].poly.points3d)[0], name

    gs, xs = _sections(gnx), _sections(xml)
    assert sorted(gs) == sorted(xs)
    for name in sorted(xs):
        assert gs[name].unique_props() == xs[name].unique_props(), name

    gm, xm = _materials(gnx), _materials(xml)
    assert sorted(gm) == sorted(xm)
    for name in sorted(xm):
        assert _mat_values(gm[name]) == pytest.approx(_mat_values(xm[name])), name


# -- the temporary directory's lifetime -------------------------------------------------


def test_topology_store_is_populated_after_the_temp_dir_is_gone(tmp_path, monkeypatch):
    """``build_topology_store=True`` reads the workspace's SAT into a neutral BRep store.

    That SAT exists only inside the temporary directory the workspace is unpacked into, so
    the store has to be built before the directory is removed. Build it after and it depends
    on whether something earlier happened to have loaded the SAT records already -- today
    reading the plates does, which is exactly why this is fragile rather than broken: the
    failure mode is silent (an empty store, no exception, no warning) and the next
    ``to_genie_xml(embed_sat=True)`` quietly re-welds the plate outlines instead of
    re-exporting the source topology.

    So the *ordering* is asserted, not only the outcome: the store must be built while the
    temporary directory still exists. Measured: moving only the store build out of the
    ``with`` (leaving the concept read inside) still produces a correct store today, so an
    outcome-only assertion does not catch it.
    """
    taken: list[pathlib.Path] = []
    real = tempfile.TemporaryDirectory

    class _Recording(real):
        def __enter__(self):
            name = super().__enter__()
            taken.append(pathlib.Path(name))
            return name

    monkeypatch.setattr(tempfile, "TemporaryDirectory", _Recording)

    from ada.cadit.sat.read import to_brep

    alive_when_built: list[bool] = []
    real_build = to_brep.sat_store_to_brep

    def _watch(sat_store):
        alive_when_built.append(any(d.exists() for d in taken))
        return real_build(sat_store)

    monkeypatch.setattr(to_brep, "sat_store_to_brep", _watch)

    gnx = tmp_path / "topo.gnx"
    _build().to_gnx(gnx)
    a = ada.from_gnx(gnx, build_topology_store=True)

    assert taken, "the workspace reader no longer unpacks into a temporary directory"
    assert alive_when_built == [True], (
        "the topology store must be built while the unpacked workspace is still on disk; "
        f"sat_store_to_brep was called {len(alive_when_built)} time(s), with the temporary "
        f"directory alive: {alive_when_built}"
    )
    assert [d for d in taken if d.exists()] == [], "a temporary directory outlived the read"

    store = a._topology_store
    assert store is not None
    summary = store.summary()
    # Measured for ``_build()``: the plate is one lump/shell/face/loop with four edges, and
    # ``bm2`` -- the beam that does not lie along a plate edge -- adds the fifth edge as a wire.
    assert summary == {
        "vertices": 5,
        "edges": 5,
        "coedges": 5,
        "loops": 1,
        "faces": 1,
        "shells": 1,
        "lumps": 1,
        "wires": 1,
        "unresolved": 0,
    }, f"the topology store is empty or wrong: {summary}"
    # The part under the assembly carries the same store; that is the one a re-export reads.
    # The part is named after the workspace, not after the part the model was built from.
    assert [p._topology_store for p in a.parts.values()] == [store]


def test_from_genie_xml_still_takes_a_workspace(tmp_path):
    """The extension sniffing stays: paths reach ``from_genie_xml`` from the CLI and the REST
    registry without anyone having looked at them, and that behaviour predates ``from_gnx``."""
    gnx = tmp_path / "compat.gnx"
    _build().to_gnx(gnx)
    a = ada.from_genie_xml(gnx)
    assert sorted(_beams(a)) == ["bm1", "bm2"]
    assert sorted(_plates(a)) == ["pl1"]
