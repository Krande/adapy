"""``ada.catalogue``: sections and materials built from a scope catalogue's rows, the client paging
through the API (a fake transport here), the whitelist check, and the E3D writer emitting the
remembered refs. The endpoint itself is tested in tests/comms/rest/test_catalogue.py."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

import ada
from ada.catalogue import (
    CATALOGUE_KEY,
    CatalogueError,
    CatalogueWhitelistError,
    check_assembly,
    scope_catalogue,
    section_dims,
)


def _material(provider: dict, override: dict | None) -> dict:
    """A row's ``material`` as core merges it (``db/catalogue.merge_material``)."""
    effective = {**provider, **(override or {})}
    return {
        "provider": provider,
        "override": override,
        "effective": effective,
        "source": {k: "override" if k in (override or {}) else "provider" for k in effective},
    }


SECTION_ROWS = [
    {"ref": "/SEC/HEA200", "name": "/SEC/HEA200", "collection": "col", "provider": "p", "use": "section",
     "spec": "/SEC", "gtype": "BEAM",
     "section": {"type": "I", "dims": {"h": 190.0, "w_top": 200.0, "t_w": 6.5, "t_ftop": 10.0}, "units": "mm",
                 "gtype": "BEAM", "source": "keys"}},
    {"ref": "/SEC/SHS150x8", "collection": "col", "provider": "p", "use": "section", "gtype": "RHS",
     "section": {"type": "BOX", "dims": {"h": 150.0, "t": 8.0}, "units": "mm", "gtype": "RHS"}},
    {"ref": "/SEC/TUB33", "collection": "col", "provider": "p", "use": "section",
     "section": {"type": "TUB", "dims": {"d": 33.4, "t": 2.77}, "units": "mm"}},
    {"ref": "/SEC/ODD", "collection": "col", "provider": "p", "use": "section", "section": None,
     "params": [{"key": "X", "value": 1.0}]},
    {"ref": "/PLT/PL10", "collection": "col", "provider": "p", "use": "plate",
     "section": {"type": "PLATE", "dims": {"t": 10.0}, "units": "mm"}},
    {"ref": "/SEC/TWICE", "collection": "col", "provider": "p", "use": "section",
     "section": {"type": "CIRC", "dims": {"d": 20.0}, "units": "mm"}},
    {"ref": "/SEC/TWICE", "collection": "other", "provider": "p", "use": "section",
     "section": {"type": "CIRC", "dims": {"d": 30.0}, "units": "mm"}},
]  # fmt: skip

MATERIAL_ROWS = [
    {"ref": "/STEEL-A", "collection": "col", "provider": "p",
     "material": _material({"family": "carbon_steel", "grade": "S355", "rho": 7850.0}, {"sig_y": 345e6, "E": 2.05e11})},
    {"ref": "/ALU-1", "collection": "col", "provider": "p",
     "material": _material({"family": "aluminium", "grade": "6082-T6", "rho": 2700.0}, None)},
]  # fmt: skip


def _transport(sections=SECTION_ROWS, materials=MATERIAL_ROWS, page=3):
    calls = []

    def get(url: str) -> dict:
        calls.append(url)
        parsed = urlparse(url)
        offset = int(parse_qs(parsed.query)["offset"][0])
        rows = sections if parsed.path.endswith("/sections") else materials
        chunk = rows[offset : offset + page]
        end = offset + len(chunk)
        return {"items": chunk, "total": len(rows), "next_offset": end if end < len(rows) else None}

    return get, calls


def _cat():
    return scope_catalogue("shared", url="https://studio.example", transport=_transport()[0])


def test_section_dims_are_completed_the_way_adapy_fills_them():
    box = section_dims("BOX", {"h": 0.15, "t": 0.008})
    assert box == {"h": 0.15, "w_top": 0.15, "t_w": 0.008, "t_ftop": 0.008, "w_btn": 0.15, "t_fbtn": 0.008}
    assert section_dims("TUB", {"d": 0.0334, "t": 0.00277}) == pytest.approx({"r": 0.0167, "wt": 0.00277})
    with pytest.raises(CatalogueError, match="needs"):
        section_dims("I", {"h": 0.2})


def test_the_client_pages_through_the_scope_catalogue_and_builds_sections():
    get, calls = _transport()
    cat = scope_catalogue("project:abc", url="https://studio.example", transport=get)

    sec = cat.section("/SEC/HEA200")
    assert isinstance(sec, ada.Section) and sec.type == ada.Section.TYPES.IPROFILE
    assert sec.name == "HEA200" and sec.h == pytest.approx(0.19) and sec.w_btn == sec.w_top
    assert sec.metadata[CATALOGUE_KEY] == {
        "ref": "/SEC/HEA200", "collection": "col", "provider": "p", "gtype": "BEAM", "spec": "/SEC", "source": "keys"
    }  # fmt: skip
    assert calls[0].startswith("https://studio.example/api/scopes/project:abc/catalogue/sections?")
    assert len([c for c in calls if "/sections" in c]) == 3, "three pages of three"

    box = cat.section("/SEC/SHS150x8")
    assert box.type == ada.Section.TYPES.BOX and box.metadata[CATALOGUE_KEY]["gtype"] == "RHS"
    assert cat.section("/SEC/TUB33").type == ada.Section.TYPES.TUBULAR
    # Listings leave out what does not build; the rows keep it.
    assert {s.name for s in cat.sections()} >= {"HEA200", "SHS150x8", "TUB33"}
    assert "/SEC/ODD" in {r["ref"] for r in cat.section_rows()}
    assert [s.name for s in cat.sections(type="BOX")] == ["SHS150x8"]
    with pytest.raises(CatalogueError, match="no section dimensions"):
        cat.section("/SEC/ODD")
    with pytest.raises(CatalogueError, match="several collections"):
        cat.section("/SEC/TWICE")
    assert cat.section("/SEC/TWICE", collection="other").r == pytest.approx(0.015)
    with pytest.raises(CatalogueError, match="not in this scope"):
        cat.section("/NOPE")
    plate = cat.plate("/PLT/PL10")
    assert plate.t == pytest.approx(0.01) and plate.metadata[CATALOGUE_KEY]["ref"] == "/PLT/PL10"


def test_materials_use_the_merged_values_and_list_what_was_defaulted():
    cat = _cat()
    steel = cat.material("/STEEL-A")
    assert steel.model.E == 2.05e11 and steel.model.sig_y == 345e6 and steel.model.rho == 7850.0
    meta = steel.metadata[CATALOGUE_KEY]
    assert meta["ref"] == "/STEEL-A" and meta["grade"] == "S355"
    # sig_u came from the grade table, v / alpha / zeta from the family: listed, not hidden.
    assert set(meta["defaulted"]) == {"sig_u", "v", "alpha", "zeta"}

    alu = cat.material("/ALU-1")
    assert alu.model.E == 7.0e10 and alu.model.sig_y is None
    with pytest.raises(CatalogueError, match="override"):
        cat.material("/ALU-1", strict=True)


def test_the_client_reads_its_url_from_the_environment(monkeypatch):
    monkeypatch.delenv("ADA_VIEWER_API_URL", raising=False)
    with pytest.raises(CatalogueError, match="ADA_VIEWER_API_URL"):
        scope_catalogue("shared")
    monkeypatch.setenv("ADA_VIEWER_API_URL", "ftp://nope")
    with pytest.raises(CatalogueError, match="http"):
        scope_catalogue("shared")


def _model(cat, *, sec_ref="/SEC/HEA200", stray=False):
    a = ada.Assembly("a")
    p = ada.Part("p")
    a.add_part(p)
    mat = cat.material("/STEEL-A")
    p.add_beam(ada.Beam("b1", (0, 0, 0), (1, 0, 0), sec=cat.section(sec_ref), mat=mat))
    pl = cat.plate("/PLT/PL10")
    p.add_plate(ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], t=pl.t, mat=mat, metadata=dict(pl.metadata)))
    if stray:
        p.add_beam(ada.Beam("b2", (0, 0, 1), (1, 0, 1), sec="IPE300"))
    return a


def test_the_whitelist_passes_a_catalogue_model_and_names_what_is_not():
    cat = _cat()
    assert check_assembly(_model(cat), cat) == []
    violations = check_assembly(_model(cat, stray=True), cat)
    assert {(v.object_name, v.what) for v in violations} == {("b2", "section"), ("b2", "material")}
    with pytest.raises(CatalogueWhitelistError, match="b2"):
        check_assembly(_model(cat, stray=True), cat, raise_on_violation=True)
    # Without a catalogue only the provenance is checked.
    assert check_assembly(_model(cat)) == []


def test_tag_plate_marks_an_existing_plate_and_refuses_another_thickness():
    cat = _cat()
    plate = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], t=0.01)
    cat.tag_plate(plate, "/PLT/PL10")
    assert plate.metadata[CATALOGUE_KEY]["ref"] == "/PLT/PL10"
    with pytest.raises(CatalogueError, match="thick"):
        cat.tag_plate(ada.Plate("pl2", [(0, 0), (1, 0), (1, 1), (0, 1)], t=0.02), "/PLT/PL10")


def test_the_e3d_writer_emits_the_catalogue_refs_and_gtype_without_maps():
    from ada.cadit.e3d.write_mac import E3DWriter

    a = _model(_cat(), sec_ref="/SEC/SHS150x8")
    writer = E3DWriter()
    beam = next(iter(a.get_all_physical_objects(by_type=ada.Beam)))
    pml = writer.create_beam_pml(beam)
    assert "GTYPE RHS" in pml and "SPREF /SEC/SHS150x8" in pml and "MATREF /STEEL-A" in pml
    plate = next(iter(a.get_all_physical_objects(by_type=ada.Plate)))
    assert "SPREF /PLT/PL10" in writer.create_plate_pml(plate)
    # A map still wins where it answers; a name without a catalogue ref still guesses its GTYPE.
    assert "SPREF /X/Y" in E3DWriter(beam_spec_map={"SHS150x8": "/X/Y"}).create_beam_pml(beam)
    plain = ada.Beam("b3", (0, 0, 0), (1, 0, 0), sec="IPE300")
    assert "GTYPE IPE" in writer.create_beam_pml(plain)
