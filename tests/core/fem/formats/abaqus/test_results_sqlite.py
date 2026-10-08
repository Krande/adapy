"""The Abaqus results SQLite: schema, exporters, and both readers, on real exports.

The fixtures (``files/fem_files/abaqus/results``) are tiny models solved with Abaqus and exported
by ``aba_io.py`` -- regenerate them with ``scripts/codegen/gen_abaqus_results_fixtures.py``:

* ``beam``: 10 x B31 cantilever; a static step (tip load) then a 4-mode frequency step
* ``shell``: 4 x 4 S4R plate under pressure
* ``solid``: 4 x C3D8 column; S at the 8 integration points, E at the element nodes
"""

import os
import shutil
import sqlite3
import sys

import numpy as np
import pytest

from ada.fem.formats.abaqus.results import read_odb
from ada.fem.formats.abaqus.results.read_odb import (
    convert_odb_to_sqlite,
    get_odb_exporter,
    read_history,
    read_results_sqlite,
    register_odb_exporter,
)
from ada.fem.formats.abaqus.results.sqlite_stream import ResultsSqliteStreamReader
from ada.fem.results.field_data import (
    ElementFieldData,
    FieldPosition,
    NodalFieldData,
    NodalFieldType,
)
from ada.fem.results.sqlite_schema import (
    SCHEMA_VERSION,
    check_schema_version,
    create_schema,
    is_results_db,
    order_components,
)
from ada.fem.results.sqlite_store import SQLiteFEAStore


@pytest.fixture
def results_dir(fem_files):
    return fem_files / "abaqus" / "results"


@pytest.fixture
def copy_of(results_dir, tmp_path):
    """A scratch copy of a fixture: the stream reader adds indexes to the file it opens."""

    def _copy(name):
        dst = tmp_path / f"{name}.sqlite"
        shutil.copyfile(results_dir / f"{name}.sqlite", dst)
        return dst

    return _copy


def _layout(conn):
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY rowid")]
    return [(t, [(c[1], c[2]) for c in conn.execute(f'PRAGMA table_info("{t}")')]) for t in tables]


# ----- schema ---------------------------------------------------------------


def test_schema_is_versioned_and_idempotent():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    create_schema(conn)  # a writer re-opening a partial export
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 1
    assert is_results_db(conn)


def test_a_newer_schema_is_refused():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(ValueError, match="Upgrade adapy"):
        check_schema_version(conn)


@pytest.mark.parametrize("name", ["beam", "shell", "solid"])
def test_fixtures_have_the_current_schema(results_dir, name):
    fresh = sqlite3.connect(":memory:")
    create_schema(fresh)
    fixture = sqlite3.connect(results_dir / f"{name}.sqlite")
    assert check_schema_version(fixture) == SCHEMA_VERSION
    regenerate = "regenerate the fixtures (scripts/codegen/gen_abaqus_results_fixtures.py)"
    assert _layout(fixture) == _layout(fresh), regenerate
    lax = [
        t
        for t, strict in fixture.execute("SELECT name, strict FROM pragma_table_list WHERE schema = 'main'")
        if not strict and not t.startswith("sqlite_")
    ]
    assert not lax, f"not STRICT: {lax}; {regenerate}"


@pytest.mark.parametrize("name", ["beam", "shell", "solid"])
def test_fixtures_carry_no_machine_details(results_dir, name):
    """The generator scrubs who exported a fixture and where its ODB was (scrub() in the script)."""
    raw = (results_dir / f"{name}.sqlite").read_bytes()
    ((project, user, filename),) = sqlite3.connect(results_dir / f"{name}.sqlite").execute("SELECT * FROM metadata")
    assert (project, user, filename) == (name, "", f"{name}.odb")
    assert b"Users" not in raw and b"AppData" not in raw, "regenerate the fixture: a path survives in the file"


def test_strict_tables_refuse_mistyped_values():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    with pytest.raises(sqlite3.IntegrityError, match="cannot store TEXT value in INTEGER column"):
        conn.execute("INSERT INTO Points (InstanceID, ID, X, Y, Z) VALUES (1, 'node 1', 0, 0, 0)")


def test_older_versions_are_read():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    for version in (0, 1):  # v0: written before versioning; v1: the same columns, untyped
        conn.execute(f"PRAGMA user_version = {version}")
        assert check_schema_version(conn) == version


def test_components_are_in_abaqus_order():
    # FieldIDs follow first use, and a history output (the tip's U2) is used before the field.
    assert [n for _, n in order_components([(0, "U2"), (5, "U1"), (6, "U3")])] == ["U1", "U2", "U3"]
    tensor = [(i, n) for i, n in enumerate(["S12", "S33", "S11", "S23", "S22", "S13"])]
    assert [n for _, n in order_components(tensor)] == ["S11", "S22", "S33", "S12", "S13", "S23"]


# ----- FEAResult ------------------------------------------------------------


def test_beam_static_then_modes(results_dir):
    res = read_results_sqlite(results_dir / "beam.sqlite")
    assert len(res.mesh.nodes.identifiers) == 11
    ((block),) = res.mesh.elements
    assert block.elem_info.source_type == "B31" and len(block.identifiers) == 10

    u = [f for f in res.results if isinstance(f, NodalFieldData) and f.name == "U"]
    # static frame 1, then the 4 modes: a mixed result is numbered by frame
    assert [f.step for f in u] == [1, 2, 3, 4, 5]
    assert all(f.components == ["U1", "U2", "U3", "UR1", "UR2", "UR3"] for f in u)
    assert all(f.field_type == NodalFieldType.DISP for f in u)
    freqs = [f.eigen_freq for f in u[1:]]
    assert u[0].eigen_freq is None and all(f > 0 for f in freqs) and freqs == sorted(freqs)

    # the static tip deflection is the same number the tip's U2 history output recorded
    tip_u2 = u[0].values[u[0].values[:, 0] == 11][0][2]
    history_u2 = [v for step, _, _, var, _, v in read_history(results_dir / "beam.sqlite") if var == "U2"]
    assert tip_u2 < 0
    assert tip_u2 == pytest.approx(history_u2[-1], rel=1e-6)

    (rf,) = [f for f in res.results if f.name == "RF"]
    assert rf.field_type == NodalFieldType.FORCE
    # the support reacts the 1 kN tip load
    assert rf.values[:, 2].sum() == pytest.approx(1000.0, rel=1e-4)

    sf = [f for f in res.results if isinstance(f, ElementFieldData) and f.name == "SF"]
    assert len(sf) == 1 and sf[0].field_pos == FieldPosition.INT
    assert sf[0].values.shape == (10, 2 + 3)  # element, integration point, SF1..SF3


def test_shell_and_solid_integration_points(results_dir):
    shell = read_results_sqlite(results_dir / "shell.sqlite")
    (s,) = [f for f in shell.results if f.name == "S"]
    assert s.components == ["S11", "S22", "S33", "S12"]
    assert s.values.shape == (16, 2 + 4)  # S4R: one integration point

    solid = read_results_sqlite(results_dir / "solid.sqlite")
    (s,) = [f for f in solid.results if f.name == "S"]
    assert s.values.shape == (4 * 8, 2 + 6)  # C3D8: eight integration points
    np.testing.assert_array_equal(s.values[:8, 1], np.arange(1, 9))


def test_history_reaches_the_legacy_results_reader(copy_of, tmp_path):
    """``Results`` (the .odb reader of ``ada.fem.results.concepts``) reads history from the SQLite.

    An .sqlite newer than its .odb is reused, so no exporter runs here.
    """
    from ada.fem.formats.abaqus.results._results import odb_data_to_results
    from ada.fem.results.concepts import Results

    odb = tmp_path / "beam.odb"
    odb.write_bytes(b"")
    db = copy_of("beam")
    os.utime(odb, (1, 1))

    results = Results.__new__(Results)
    odb_data_to_results(odb, results)
    step_names = [s.name for s in results.history_output.steps]
    assert step_names == ["static", "modes"]
    assert "U2" in results.history_output.steps[0].fem_data
    assert db.exists()


def test_store_reads_the_blob_tables(copy_of):
    store = SQLiteFEAStore(copy_of("beam"))
    rows = store.get_field_nodal_data("U2")
    assert {r[1] for r in rows} == {"static", "modes"}
    inst, step, var, frame, values = rows[0]
    assert var == "U2" and values.shape == (11,)
    (row, *_) = store.get_field_elem_data("SF1", location="INTEGRATION_POINT")
    assert row[7].shape == (10, 1)
    assert len(store.get_history_data("EIGFREQ")) == 4


# ----- streaming reader -----------------------------------------------------


def test_stream_reader_beam(copy_of):
    with ResultsSqliteStreamReader(copy_of("beam")) as reader:
        geom = reader.read_mesh_geometry()
        assert geom.points.shape == (11, 3)
        assert [(c.cell_type, c.data.shape) for c in geom.cell_blocks] == [("line", (10, 2))]

        specs = {s.name: s for s in reader.field_specs()}
        disp = specs["Spatial displacement"]
        assert disp.components == ["U1", "U2", "U3"] and disp.category == "displacement"
        last = list(reader.iter_field_steps(disp.name))[-1]
        assert last.values.shape == (11, 3)

        history = reader.try_history_records()
        assert history is not None
        assert {v.name_native for v in history.variables} >= {"U2", "EIGFREQ"}


def test_stream_reader_element_nodal(copy_of):
    with ResultsSqliteStreamReader(copy_of("solid")) as reader:
        specs = {(s.name, s.support): s for s in reader.element_field_specs()}
        strain = specs[("Strain components", "element_nodal")]
        stress = specs[("Stress components", "gauss")]
        assert strain.n_ips == 8 and stress.n_ips == 8  # 8 element nodes, 8 integration points
        values = list(reader.iter_element_field_steps(strain))[-1].values
        assert values.shape == (4, 8, 6)
        # one value per element node: the column bends, so the nodes of an element differ
        assert len(np.unique(np.round(values[0, :, 0], 12))) > 1


def test_stream_reader_refuses_other_sqlite(tmp_path):
    other = tmp_path / "model.sqlite"
    sqlite3.connect(other).execute("CREATE TABLE ifc (id INTEGER)")
    with pytest.raises(ValueError, match="not an FEA results sqlite"):
        ResultsSqliteStreamReader(other)


def test_register_is_opt_in(copy_of, monkeypatch):
    from ada.fem.formats.abaqus.results import sqlite_stream
    from ada.fem.results import artefacts
    from ada.fem.results.artefacts import readers

    # Built-ins first: registering them is a one-shot, and done into the copy it would leave the
    # restored registry without them for every later test.
    readers._ensure_builtin_stream_readers()
    monkeypatch.setattr(readers, "_STREAM_READERS", dict(readers._STREAM_READERS))
    assert ".sqlite" not in artefacts.fea_artefact_extensions()
    assert ".odb" not in artefacts.fea_artefact_extensions()

    sqlite_stream.register(odb=False)
    assert ".sqlite" in artefacts.fea_artefact_extensions() and ".odb" not in artefacts.fea_artefact_extensions()
    with artefacts.make_stream_reader(copy_of("shell")) as reader:
        assert reader.read_mesh_geometry().points.shape == (25, 3)


# ----- ODB exporters --------------------------------------------------------


@pytest.fixture
def exporters(monkeypatch):
    """An isolated exporter registry with the built-ins, no Abaqus and no export command."""
    read_odb._load_odb_exporters()
    monkeypatch.setattr(read_odb, "_ODB_EXPORTERS", dict(read_odb._ODB_EXPORTERS))
    monkeypatch.delenv(read_odb.ODB_EXPORT_CMD_ENV, raising=False)
    monkeypatch.delenv(read_odb.ODB_EXPORTER_ENV, raising=False)
    monkeypatch.setattr(read_odb, "_abaqus_exe", lambda: None)


def test_no_exporter_available(exporters):
    assert [e.name for e in read_odb.odb_exporters()][:2] == ["command", "abaqus-python"]
    assert not read_odb.odb_exporter_available()
    with pytest.raises(FileNotFoundError, match="no ODB -> SQLite exporter"):
        get_odb_exporter()


def test_registered_exporter_runs(exporters, results_dir, tmp_path):
    calls = []

    def export(odb, sqlite):
        calls.append((odb.name, sqlite.name))
        shutil.copyfile(results_dir / "beam.sqlite", sqlite)

    register_odb_exporter("test", export, prefer=True)
    odb = tmp_path / "job.odb"
    odb.write_bytes(b"")
    db = convert_odb_to_sqlite(odb)
    assert calls == [("job.odb", "job.sqlite")] and db == odb.with_suffix(".sqlite").resolve()
    convert_odb_to_sqlite(odb)  # newer than the ODB: reused
    assert len(calls) == 1
    convert_odb_to_sqlite(odb, overwrite=True)
    assert len(calls) == 2


def test_command_exporter(exporters, results_dir, tmp_path, monkeypatch):
    script = tmp_path / "exporter.py"
    script.write_text(f"import shutil, sys\nshutil.copyfile(r'{results_dir / 'shell.sqlite'}', sys.argv[2])\n")
    monkeypatch.setenv(read_odb.ODB_EXPORT_CMD_ENV, f'"{sys.executable}" "{script}" {{odb}} {{sqlite}}')
    assert get_odb_exporter().name == "command"

    odb = tmp_path / "plate.odb"
    odb.write_bytes(b"")
    result = read_odb.read_odb(odb)
    assert len(result.mesh.nodes.identifiers) == 25


def test_exporter_chosen_by_name(exporters, monkeypatch):
    monkeypatch.setenv(read_odb.ODB_EXPORTER_ENV, "abaqus-python")
    assert get_odb_exporter().name == "abaqus-python"
    monkeypatch.setenv(read_odb.ODB_EXPORTER_ENV, "nope")
    with pytest.raises(KeyError, match="no ODB exporter"):
        get_odb_exporter()
