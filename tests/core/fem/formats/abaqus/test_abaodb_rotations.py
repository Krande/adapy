"""Abaqus UR is read as the rotational part of U, as the other solvers' displacements carry theirs.

Kept as a separate field it had an unknown type, and the bake drops every non-displacement field --
so an Abaqus beam model reached the viewer without rotations, and a torsion mode had nothing to
draw.
"""

import sqlite3

import numpy as np

from ada.fem.formats.abaqus.post_processing import read_abaodb_sqlite
from ada.fem.results.field_data import NodalFieldType

_SCHEMA = """
CREATE TABLE Points (InstanceID INTEGER, ID INTEGER, X REAL, Y REAL, Z REAL);
CREATE TABLE ElementConnectivity (InstanceID INTEGER, ElemID INTEGER, PointID INTEGER, Seq INTEGER);
CREATE TABLE ElementInfo (InstanceID INTEGER, ElemID INTEGER, Type TEXT);
CREATE TABLE FieldVars (FieldID INTEGER, Name TEXT);
CREATE TABLE HistOutput (FieldVarID INTEGER, Frame INTEGER, Value REAL);
CREATE TABLE Frames (StepID INTEGER, FrameID INTEGER, FrameValue REAL);
CREATE TABLE FieldNodes (StepID INTEGER, Frame REAL, FieldVarID INTEGER, InstanceID INTEGER,
                         IsImaginary INTEGER, Data BLOB);
"""

_VARS = ["U1", "U2", "U3", "UR1", "UR2", "UR3", "EIGFREQ", "EIGVAL"]


def _db(tmp_path, with_ur=True):
    path = tmp_path / "beam.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.executemany("INSERT INTO Points VALUES (1, ?, ?, 0, 0)", [(1, 0.0), (2, 1.0)])
    conn.executemany("INSERT INTO ElementConnectivity VALUES (1, 1, ?, ?)", [(1, 0), (2, 1)])
    conn.execute("INSERT INTO ElementInfo VALUES (1, 1, 'B31')")
    conn.executemany("INSERT INTO FieldVars VALUES (?, ?)", list(enumerate(_VARS, start=1)))
    var_id = {name: i for i, name in enumerate(_VARS, start=1)}
    conn.executemany("INSERT INTO HistOutput VALUES (?, 1, ?)", [(var_id["EIGFREQ"], 10.0), (var_id["EIGVAL"], 3947.8)])
    conn.execute("INSERT INTO Frames VALUES (1, 1, 1.0)")
    # A torsion mode: no translation, node 2 twisted 1 rad about X.
    values = {"U1": [0, 0], "U2": [0, 0], "U3": [0, 0], "UR1": [0, 1], "UR2": [0, 0], "UR3": [0, 0]}
    for name, vals in values.items():
        if name.startswith("UR") and not with_ur:
            continue
        blob = np.asarray(vals, dtype="<f4").tobytes()
        conn.execute("INSERT INTO FieldNodes VALUES (1, 1.0, ?, 1, 0, ?)", (var_id[name], blob))
    conn.commit()
    conn.close()
    return path


def test_ur_is_read_as_the_rotations_of_u(tmp_path):
    result = read_abaodb_sqlite(_db(tmp_path))
    (u,) = result.results
    assert u.name == "U"
    assert u.field_type == NodalFieldType.DISP
    assert u.components == ["U1", "U2", "U3", "UR1", "UR2", "UR3"]
    # node id, then the six components; node 2's twist about X survives.
    np.testing.assert_allclose(u.values[1], [2, 0, 0, 0, 1, 0, 0])
    assert u.eigen_freq == 10.0


def test_without_ur_u_is_translation_only(tmp_path):
    (u,) = read_abaodb_sqlite(_db(tmp_path, with_ur=False)).results
    assert u.components == ["U1", "U2", "U3"]
