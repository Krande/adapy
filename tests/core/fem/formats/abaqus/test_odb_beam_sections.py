"""Beam sections from an ODB: exported by ``aba_io.py``, read back as profiles, baked as beam solids.

Abaqus is not available here, so the ODB is a stand-in with the ODB API's attribute names; the
exporter itself (``OdbExporter``) runs unchanged on it and writes a real results SQLite. What the
stand-ins cannot prove is that a real ODB names things this way -- the profile classes and
attributes follow the Abaqus Scripting Reference (IProfile l h b1 b2 t1 t2 t3, BoxProfile a b
uniformThickness t t1..t4, ...; SectionAssignment region / sectionName; BeamOrientation region /
method / vector).
"""

import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from ada.fem.formats.abaqus.results import aba_io
from ada.fem.formats.abaqus.results.read_odb import read_results_sqlite
from ada.fem.formats.abaqus.results.sqlite_sections import (
    local_z_from_n1,
    profile_to_section,
    read_line_section_tables,
)
from ada.fem.formats.abaqus.results.sqlite_stream import ResultsSqliteStreamReader
from ada.fem.results.artefacts import BeamSolidInstances, bake_artefacts
from ada.fem.results.sqlite_schema import create_schema
from ada.sections.categories import BaseTypes

# ----- ODB stand-ins ----------------------------------------------------------------------------


class _Profile:
    def __init__(self, name, **dims):
        self.name = name
        for k, v in dims.items():
            setattr(self, k, v)


class IProfile(_Profile):
    pass


class BoxProfile(_Profile):
    pass


class PipeProfile(_Profile):
    pass


class LProfile(_Profile):
    pass


class GeneralizedProfile(_Profile):
    pass


class ArbitraryProfile(_Profile):
    pass


class BeamSection(SimpleNamespace):
    pass


def _node(label, xyz):
    return SimpleNamespace(label=label, coordinates=xyz)


def _elem(label, nodes, el_type="B31"):
    return SimpleNamespace(label=label, type=el_type, connectivity=nodes)


def _set(name, elements):
    return SimpleNamespace(name=name, elements=elements)


def _odb():
    """A 5-element frame: two I beams along X (one with n1 given, one on Abaqus' default), a box
    column (n1 given), an L brace, and one beam on a generalized section (no outline)."""
    nodes = [
        _node(1, (0.0, 0.0, 0.0)),
        _node(2, (2.0, 0.0, 0.0)),
        _node(3, (4.0, 0.0, 0.0)),
        _node(4, (4.0, 0.0, 3.0)),
        _node(5, (2.0, 2.0, 0.0)),
        _node(6, (2.0, 4.0, 0.0)),
    ]
    elements = [
        _elem(1, (1, 2)),
        _elem(2, (2, 3)),
        _elem(3, (3, 4)),
        _elem(4, (2, 5)),
        _elem(5, (5, 6)),
    ]
    by_label = {e.label: e for e in elements}
    girder = _set("GIRDER", [by_label[1], by_label[2]])
    column = _set("COLUMN", [by_label[3]])
    # Assignment regions are often sets the instance does not list (CAE's internal ones).
    brace = _set(" ALL BRACES", [by_label[4]])
    gen = _set("GEN", [by_label[5]])
    inst = SimpleNamespace(
        name="PART-1-1",
        nodes=nodes,
        elements=elements,
        elementSets={"GIRDER": girder, "COLUMN": column, "GEN": gen},
        nodeSets={},
        sectionAssignments=[
            SimpleNamespace(region=girder, sectionName="SEC-I", offset=0.0),
            SimpleNamespace(region=column, sectionName="SEC-BOX", offset=0.0),
            SimpleNamespace(region=brace, sectionName="SEC-L", offset=0.0),
            SimpleNamespace(region=gen, sectionName="SEC-GEN", offset=0.0),
        ],
        beamOrientations=[
            SimpleNamespace(region=_set("GIRDER-1", [by_label[1]]), method="N1_COSINES", vector=(0.0, 1.0, 0.0)),
            SimpleNamespace(region=column, method="N1_COSINES", vector=(1.0, 0.0, 0.0)),
        ],
    )
    assembly = SimpleNamespace(nodes=[], elements=[], elementSets={}, nodeSets={}, instances={"PART-1-1": inst})
    steel = SimpleNamespace(
        description="",
        density=SimpleNamespace(table=((7850.0,),)),
        elastic=SimpleNamespace(type="ISOTROPIC", table=((2.1e11, 0.3),)),
    )
    profiles = {
        "IPE300": IProfile("IPE300", l=0.15, h=0.3, b1=0.15, b2=0.15, t1=0.0107, t2=0.0107, t3=0.0071),
        "BOX200": BoxProfile("BOX200", a=0.2, b=0.3, uniformThickness="ON", t=0.01, t1=0.0, t2=0.0, t3=0.0, t4=0.0),
        "L100": LProfile("L100", a=0.1, b=0.12, t1=0.01, t2=0.008),
        "GEN": GeneralizedProfile("GEN", area=0.01, i11=1e-5, i12=0.0, i22=1e-5, j=1e-6, gammaO=0.0, gammaW=0.0),
    }
    sections = {
        "SEC-I": BeamSection(profile="IPE300", material="STEEL"),
        "SEC-BOX": BeamSection(profile="BOX200", material="STEEL"),
        "SEC-L": BeamSection(profile="L100", material="STEEL"),
        "SEC-GEN": BeamSection(profile="GEN", material="STEEL"),
        "SEC-SHELL": SimpleNamespace(thickness=0.01, material="STEEL"),
    }
    return SimpleNamespace(
        rootAssembly=assembly, steps={}, materials={"STEEL": steel}, profiles=profiles, sections=sections
    )


@pytest.fixture
def odb_sqlite(tmp_path):
    """The stand-in ODB exported by the real exporter."""
    path = tmp_path / "frame.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    aba_io.OdbExporter(_odb(), conn).export("frame.odb")
    conn.close()
    return path


# ----- exporter: ODB objects -> rows -------------------------------------------------------------


def test_profiles_are_exported_with_their_dimensions():
    d = aba_io.profile_dict(IProfile("IPE300", l=0.15, h=0.3, b1=0.15, b2=0.15, t1=0.01, t2=0.01, t3=0.007))
    assert d == {"type": "I", "name": "IPE300", "l": 0.15, "h": 0.3, "b1": 0.15, "b2": 0.15} | {
        "t1": 0.01,
        "t2": 0.01,
        "t3": 0.007,
    }
    box = aba_io.profile_dict(BoxProfile("B", a=0.2, b=0.3, uniformThickness="ON", t=0.01))
    assert (box["type"], box["t1"], box["t2"], box["t3"], box["t4"]) == ("BOX", 0.01, 0.01, 0.01, 0.01)
    box = aba_io.profile_dict(BoxProfile("B", a=0.2, b=0.3, uniformThickness="OFF", t=0.0, t1=1, t2=2, t3=3, t4=4))
    assert [box[k] for k in ("t1", "t2", "t3", "t4")] == [1.0, 2.0, 3.0, 4.0]
    table = ((0.1, -0.1), (0.0, -0.1, 0.01), (0.0, 0.1, 0.006))
    assert aba_io.profile_dict(ArbitraryProfile("A", table=table))["table"] == [list(r) for r in table]
    assert aba_io.profile_dict(PipeProfile("P", r=0.1, t=0.01)) == {"type": "PIPE", "name": "P", "r": 0.1, "t": 0.01}

    class FancyProfile(_Profile):
        pass

    assert aba_io.profile_dict(FancyProfile("F"))["type"] == "UNKNOWN"


def test_material_and_section_rows():
    steel = _odb().materials["STEEL"]
    assert aba_io.material_row("STEEL", steel) == dict(
        Name="STEEL", Description=None, Density=7850.0, YoungsModulus=2.1e11, PoissonRatio=0.3
    )
    bare = aba_io.material_row("X", SimpleNamespace(density=None, elastic=None))
    assert (bare["Density"], bare["YoungsModulus"], bare["PoissonRatio"]) == (None, None, None)
    ortho = SimpleNamespace(density=None, elastic=SimpleNamespace(type="ENGINEERING_CONSTANTS", table=((1, 2, 3),)))
    assert aba_io.material_row("O", ortho)["YoungsModulus"] is None

    odb = _odb()
    row = aba_io.section_row("SEC-I", odb.sections["SEC-I"], odb.profiles)
    assert row["MaterialName"] == "STEEL" and json.loads(row["Profile"])["type"] == "I"
    shell = aba_io.section_row("SEC-SHELL", odb.sections["SEC-SHELL"], odb.profiles)
    assert (shell["Thickness"], shell["Profile"]) == (0.01, None)
    missing = aba_io.section_row("S", BeamSection(profile="NOPE", material="STEEL"), odb.profiles)
    assert json.loads(missing["Profile"]) == {"type": "MISSING", "name": "NOPE"}


def test_beam_orientation_row():
    bo = SimpleNamespace(method="N1_COSINES", vector=(0.0, 1.0, 0.0))
    row = aba_io.beam_orientation_row(1, "S", bo)
    assert (row["Kind"], row["CsysType"], row["XAxisX"], row["XAxisY"], row["XAxisZ"]) == (
        "beam",
        "N1_COSINES",
        0.0,
        1.0,
        0.0,
    )
    assert aba_io.beam_orientation_row(1, "S", SimpleNamespace(method="CSYS", vector=None)) is None


def test_export_writes_the_property_tables(odb_sqlite):
    conn = sqlite3.connect(odb_sqlite)
    assert conn.execute("SELECT Name, Density FROM Materials").fetchall() == [("STEEL", 7850.0)]
    profiles = dict(conn.execute("SELECT Name, Profile FROM Sections WHERE Profile IS NOT NULL").fetchall())
    assert {k: json.loads(v)["type"] for k, v in profiles.items()} == {
        "SEC-I": "I",
        "SEC-BOX": "BOX",
        "SEC-L": "L",
        "SEC-GEN": "GENERALIZED",
    }
    assignments = conn.execute("SELECT InstanceID, SetName, SectionName FROM SectionAssignments").fetchall()
    assert assignments == [
        (1, "GIRDER", "SEC-I"),
        (1, "COLUMN", "SEC-BOX"),
        (1, "ALL BRACES", "SEC-L"),
        (1, "GEN", "SEC-GEN"),
    ]
    # A region that is not one of the instance's element sets gets its membership written.
    assert conn.execute("SELECT ElemID FROM ElementSets WHERE Name = 'ALL BRACES'").fetchall() == [(4,)]
    beams = conn.execute(
        "SELECT SetName, XAxisX, XAxisY, XAxisZ FROM MaterialOrientations WHERE Kind = 'beam'"
    ).fetchall()
    assert beams == [("GIRDER-1", 0.0, 1.0, 0.0), ("COLUMN", 1.0, 0.0, 0.0)]


# ----- reader: rows -> adapy sections ------------------------------------------------------------


@pytest.mark.parametrize(
    "profile, base_type",
    [
        ({"type": "I", "l": 0.15, "h": 0.3, "b1": 0.15, "b2": 0.15, "t1": 0.01, "t2": 0.01, "t3": 0.007}, "IPROFILE"),
        ({"type": "I", "l": 0.15, "h": 0.3, "b1": 0.0, "b2": 0.15, "t1": 0.0, "t2": 0.01, "t3": 0.007}, "TPROFILE"),
        ({"type": "T", "b": 0.15, "h": 0.3, "l": 0.15, "tf": 0.01, "tw": 0.007}, "TPROFILE"),
        ({"type": "BOX", "a": 0.2, "b": 0.3, "t1": 0.01, "t2": 0.01, "t3": 0.01, "t4": 0.01}, "BOX"),
        ({"type": "PIPE", "r": 0.1, "t": 0.01}, "TUBULAR"),
        ({"type": "CIRC", "r": 0.1}, "CIRCULAR"),
        ({"type": "RECT", "a": 0.02, "b": 0.1}, "FLATBAR"),
        ({"type": "L", "a": 0.1, "b": 0.12, "t1": 0.01, "t2": 0.008}, "ANGULAR"),
        (
            {
                "type": "ARBITRARY",
                "table": [[0.095, -0.095], [0.0, -0.095, 0.01], [0.0, 0.095, 0.006], [0.095, 0.095, 0.01]],
            },
            "CHANNEL",
        ),
    ],
)
def test_profiles_map_to_adapy_sections(profile, base_type):
    sec, reason = profile_to_section("S", profile)
    assert reason is None
    assert sec.type == BaseTypes[base_type]


@pytest.mark.parametrize("kind", ["GENERALIZED", "TRAPEZOID", "HEX", "MISSING", "UNKNOWN"])
def test_profiles_without_an_outline_are_refused_with_a_reason(kind):
    sec, reason = profile_to_section("S", {"type": kind})
    assert sec is None and reason == f"unsupported-profile[{kind.lower()}]"


def test_box_dimensions_follow_the_input_deck_reader():
    sec, _ = profile_to_section(
        "S", {"type": "BOX", "a": 0.2, "b": 0.3, "t1": 0.01, "t2": 0.02, "t3": 0.01, "t4": 0.03}
    )
    assert (sec.w_top, sec.h, sec.t_w, sec.t_ftop, sec.t_fbtn) == (0.2, 0.3, 0.01, 0.02, 0.03)


def test_local_z_is_n2():
    # n2 = t x n1: a beam along X with n1 = +Y has its web (n2) along +Z ...
    assert np.allclose(local_z_from_n1([2, 0, 0], (0, 1, 0)), [0, 0, 1])
    # ... and on Abaqus' default n1 (0, 0, -1) along +Y -- Abaqus lays an I on its side.
    assert np.allclose(local_z_from_n1([2, 0, 0], (0, 0, -1)), [0, 1, 0])
    # n1 along the beam has no n2; adapy's default up for the axis instead.
    assert np.allclose(local_z_from_n1([0, 0, 3], (0, 0, -1)), [0, 1, 0])


def test_tables_resolve_every_drawable_beam(odb_sqlite):
    conn = sqlite3.connect(odb_sqlite)
    tables, skipped = read_line_section_tables(conn, 1)
    assert skipped == {"unsupported-profile[generalized]": 1}
    by_elem = {int(r[0]): r for r in tables.elem_data}
    assert sorted(by_elem) == [1, 2, 3, 4]
    sec_of = {el: tables.sections[int(r[2])] for el, r in by_elem.items()}
    assert sec_of[1].type == BaseTypes.IPROFILE and sec_of[1] is sec_of[2]
    assert sec_of[3].type == BaseTypes.BOX and sec_of[4].type == BaseTypes.ANGULAR
    z_of = {el: tables.vectors[int(r[3])] for el, r in by_elem.items()}
    assert np.allclose(z_of[1], [0, 0, 1])  # n1 given
    assert np.allclose(z_of[2], [0, 1, 0])  # Abaqus' default n1
    assert np.allclose(z_of[3], [0, 1, 0])  # column along Z, n1 = X: n2 = Z x X = Y
    mat = tables.materials[int(by_elem[1][1])]
    assert (mat.name, mat.model.E, mat.model.rho) == ("STEEL", 2.1e11, 7850.0)
    assert tables.end_nodes.tolist() == [[1, 2], [2, 3], [3, 4], [2, 5]]


def test_a_database_without_sections_has_no_tables(odb_sqlite):
    conn = sqlite3.connect(odb_sqlite)
    conn.execute("UPDATE Sections SET Profile = NULL")
    assert read_line_section_tables(conn, 1) == (None, {})


# ----- both readers and the bake -----------------------------------------------------------------


def test_fea_result_mesh_carries_the_sections(odb_sqlite):
    res = read_results_sqlite(odb_sqlite)
    assert len(res.mesh.elem_data) == 4
    elems = {e.id: e for e in res.mesh.get_line_elems()}
    assert elems[3].fem_sec.section.type == BaseTypes.BOX
    assert elems[5].fem_sec is None  # the generalized one stays a line


def test_stream_reader_builds_beam_solids(odb_sqlite):
    with ResultsSqliteStreamReader(odb_sqlite) as reader:
        inst = reader.try_solid_beams(format="compact")
    assert isinstance(inst, BeamSolidInstances)
    assert sorted(int(x) for x in inst.label) == [1, 2, 3, 4]
    assert inst.skip_reasons == {"unsupported-profile[generalized]": 1} and inst.total_beams == 5


def test_bake_of_an_odb_export_offers_beam_solids(odb_sqlite, tmp_path):
    # bake_fea_artefacts_from_source(.sqlite) needs the opt-in sqlite_stream.register(); the bake is the same.
    with ResultsSqliteStreamReader(odb_sqlite) as reader:
        bake = bake_artefacts(reader, tmp_path / "out", src="frame")
    mesh = json.loads(bake.manifest_path.read_text())["mesh"]
    assert mesh["beam_solids_compact_url"] and mesh["n_beam_solids"] == 4
    assert (bake.out_dir / mesh["beam_solids_compact_url"]).is_file()
