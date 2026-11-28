"""Selecting an IFC backend per operation: argument, then ADAPY_IFC_BACKEND, then capabilities."""

from __future__ import annotations

import pytest

import ada
from ada.cadit.ifc import backend as ib


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(ib.ENV_VAR, raising=False)


def _native_ok() -> bool:
    return ib.NativeIfcBackend().available()


def test_prefer_wins(no_env, monkeypatch):
    monkeypatch.setenv(ib.ENV_VAR, "native")
    assert ib.select_ifc_backend("ifcopenshell").name == "ifcopenshell"
    assert ib.select_ifc_backend("adacpp").name == "native"


def test_unknown_backend_raises(no_env):
    with pytest.raises(ValueError):
        ib.select_ifc_backend("nope")


def test_full_read_and_write_pick_ifcopenshell(no_env):
    # Only ifcopenshell reads and writes every element kind.
    assert ib.select_ifc_backend(operation=ib.OP_READ, require={ib.CAP_READ_TYPED}).name == "ifcopenshell"
    assert ib.select_ifc_backend(operation=ib.OP_WRITE, require={ib.CAP_WRITE_TYPED}).name == "ifcopenshell"
    assert ib.select_ifc_backend(operation=ib.OP_AUTHOR, require={ib.CAP_AUTHOR}).name == "ifcopenshell"


@pytest.mark.skipif(not _native_ok(), reason="adacpp without IfcNgeomStream")
def test_auto_prefers_native_for_what_it_covers(no_env):
    assert ib.select_ifc_backend(operation=ib.OP_READ, require={ib.CAP_READ_SHAPES}).name == "native"
    caps = ib.NativeIfcBackend().capabilities()
    if ib.CAP_TESSELLATE_GLB in caps:
        assert ib.select_ifc_backend(operation=ib.OP_TESSELLATE, require={ib.CAP_TESSELLATE_GLB}).name == "native"


@pytest.mark.skipif(not _native_ok(), reason="adacpp without IfcNgeomStream")
def test_env_selects_native_when_it_can_do_the_operation(no_env, monkeypatch):
    monkeypatch.setenv(ib.ENV_VAR, "native")
    assert ib.select_ifc_backend(operation=ib.OP_READ, require={ib.CAP_READ_TYPED}).name == "native"
    # The native backend cannot author, so the env preference falls through.
    assert ib.select_ifc_backend(operation=ib.OP_AUTHOR).name == "ifcopenshell"


@pytest.mark.skipif(not _native_ok(), reason="adacpp without IfcNgeomStream")
def test_without_ifcopenshell_auto_falls_back_to_native(no_env, monkeypatch):
    monkeypatch.setattr(ib.IfcOpenShellBackend, "available", lambda self: False)
    assert ib.select_ifc_backend(operation=ib.OP_READ, require={ib.CAP_READ_TYPED}).name == "native"
    with pytest.raises(ImportError):
        ib.select_ifc_backend(operation=ib.OP_AUTHOR)


@pytest.mark.skipif(not _native_ok(), reason="adacpp without IfcNgeomStream")
def test_read_ifc_follows_the_env(no_env, monkeypatch, tmp_path):
    src = tmp_path / "m.ifc"
    (ada.Assembly("a") / (ada.Part("p") / ada.Beam("bm0", (0, 0, 0), (1, 0, 0), "IPE300"))).to_ifc(src)

    monkeypatch.setenv(ib.ENV_VAR, "native")
    a = ada.from_ifc(src)
    assert a._ifc_store is None  # the native reader built no IfcStore
    assert [o.name for o in a.get_all_physical_objects()] == ["bm0"]

    monkeypatch.delenv(ib.ENV_VAR)
    b = ada.from_ifc(src)
    assert [o.name for o in b.get_all_physical_objects()] == ["bm0"]
    assert b._ifc_store is not None


def _members_file(tmp_path):
    src = tmp_path / "members.ifc"
    beams = [ada.Beam(f"bm{i}", (i, 0, 0), (i, 0, 1), "IPE300") for i in range(2)]
    plate = ada.Plate("pl0", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    (ada.Assembly("a") / (ada.Part("p") / [*beams, plate])).to_ifc(src)
    return src


@pytest.mark.skipif(
    ib.CAP_READ_MEMBERS not in ib.NativeIfcBackend().capabilities(), reason="adacpp without member records"
)
def test_members_model_is_read_natively_when_that_is_complete(no_env, tmp_path):
    a = ib.read_ifc_members_model(_members_file(tmp_path))
    assert a._ifc_store is None  # native: no IfcStore was built
    assert sorted((type(o).__name__, o.name) for o in a.get_all_physical_objects()) == [
        ("Beam", "bm0"),
        ("Beam", "bm1"),
        ("Plate", "pl0"),
    ]


@pytest.mark.skipif(not _native_ok(), reason="adacpp without IfcNgeomStream")
def test_members_model_falls_back_when_a_member_stays_a_shape(no_env, tmp_path, monkeypatch):
    monkeypatch.setattr(ib.NativeIfcBackend, "capabilities", lambda self: frozenset({ib.CAP_READ_MEMBERS}))
    monkeypatch.setattr(ib, "native_read_covers_members", lambda a: False)
    a = ib.read_ifc_members_model(_members_file(tmp_path))
    assert a._ifc_store is not None  # read again by ifcopenshell
    assert sorted(type(o).__name__ for o in a.get_all_physical_objects()) == ["Beam", "Beam", "Plate"]


@pytest.mark.skipif(not _native_ok(), reason="adacpp without IfcNgeomStream")
def test_native_read_records_member_classes(no_env, tmp_path):
    from ada.cadit.ifc.read.native_members import native_members_available

    if not native_members_available():
        pytest.skip("adacpp without IfcMemberScan")
    a = ada.from_ifc(_members_file(tmp_path), reader="native")
    assert sorted(a._native_ifc_classes.values()) == ["IFCBEAM", "IFCBEAM", "IFCPLATE"]
    # Before adacpp 0.48 the members are shapes, and that shows.
    covered = ib.native_read_covers_members(a)
    assert covered == all(type(o).__name__ in ("Beam", "Plate") for o in a.get_all_physical_objects())


def test_query_ifcopenshell(no_env, tmp_path):
    bm = ada.Beam("bm0", (0, 0, 0), (1, 0, 0), "IPE300", metadata={"props": {"a": 1.0}})
    src = tmp_path / "m.ifc"
    (ada.Assembly("a") / (ada.Part("p") / bm)).to_ifc(src)
    recs = {r.guid: r for r in ib.get_ifc_backend("ifcopenshell").query(src)}
    assert recs[bm.guid].ifc_class == "IfcBeam"
    assert recs[bm.guid].name == "bm0"
    assert recs[bm.guid].parent == "p"


@pytest.mark.skipif(not _native_ok(), reason="adacpp without IfcNgeomStream")
def test_query_native_matches_ifcopenshell_on_names_and_guids(no_env, tmp_path):
    beams = [ada.Beam(f"bm{i}", (i, 0, 0), (i, 0, 1), "IPE300") for i in range(3)]
    src = tmp_path / "m.ifc"
    (ada.Assembly("a") / (ada.Part("p") / beams)).to_ifc(src)
    ios = {r.guid: r.name for r in ib.get_ifc_backend("ifcopenshell").query(src) if r.ifc_class == "IfcBeam"}
    nat = {r.guid: r.name for r in ib.get_ifc_backend("native").query(src)}
    assert nat == ios
