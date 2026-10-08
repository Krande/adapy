"""Abaqus UR is read as the rotational part of U, as the other solvers' displacements carry theirs.

Kept as a separate field it had an unknown type, and the bake drops every non-displacement field --
so an Abaqus beam model reached the viewer without rotations, and a torsion mode had nothing to
draw.
"""

import sqlite3

import numpy as np

from ada.fem.formats.abaqus.results.read_odb import read_results_sqlite
from ada.fem.results.field_data import NodalFieldType
from ada.fem.results.sqlite_schema import create_schema

_VARS = ["U1", "U2", "U3", "UR1", "UR2", "UR3", "EIGFREQ", "EIGVAL"]
_DESC = {"U": "Spatial displacement", "UR": "Rotational displacement", "EIG": "Eigenfrequency"}


def _db(tmp_path, with_ur=True):
    path = tmp_path / "beam.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.execute("INSERT INTO ModelInstances (ID, Name) VALUES (1, 'PART-1-1')")
    conn.executemany("INSERT INTO Points (InstanceID, ID, X, Y, Z) VALUES (1, ?, ?, 0, 0)", [(1, 0.0), (2, 1.0)])
    conn.executemany(
        "INSERT INTO ElementConnectivity (InstanceID, ElemID, PointID, Seq) VALUES (1, 1, ?, ?)", [(1, 0), (2, 1)]
    )
    conn.execute("INSERT INTO ElementInfo (InstanceID, ElemID, Type, IntPoints) VALUES (1, 1, 'B31', 1)")
    var_id = {name: i for i, name in enumerate(_VARS)}
    conn.executemany(
        "INSERT INTO FieldVars (FieldID, Name, Description) VALUES (?, ?, ?)",
        [(i, name, _DESC[name.rstrip("0123456789")[:3]]) for name, i in var_id.items()],
    )
    conn.execute("INSERT INTO Steps (ID, Name, DomainType, Procedure) VALUES (0, 'modes', 'MODAL', '*FREQUENCY')")
    conn.execute("INSERT INTO Frames (StepID, FrameID, Increment, FrameValue) VALUES (0, 1, 1, 1.0)")
    conn.executemany(
        "INSERT INTO HistOutput (Region, ResType, InstanceID, ElemID, PointID, StepID, FieldVarID, Frame, Value) "
        "VALUES ('Assembly ASSEMBLY', 'WHOLE_MODEL', 0, -1, -1, 0, ?, 1, ?)",
        [(var_id["EIGFREQ"], 10.0), (var_id["EIGVAL"], 3947.8)],
    )
    # A torsion mode: no translation, node 2 twisted 1 rad about X.
    values = {"U1": [0, 0], "U2": [0, 0], "U3": [0, 0], "UR1": [0, 1], "UR2": [0, 0], "UR3": [0, 0]}
    for name, vals in values.items():
        if name.startswith("UR") and not with_ur:
            continue
        blob = np.asarray(vals, dtype="<f4").tobytes()
        conn.execute(
            "INSERT INTO FieldNodes (InstanceID, StepID, FieldVarID, Frame, IsImaginary, Data) "
            "VALUES (1, 0, ?, 1.0, 0, ?)",
            (var_id[name], blob),
        )
    conn.commit()
    conn.close()
    return path


def test_ur_is_read_as_the_rotations_of_u(tmp_path):
    result = read_results_sqlite(_db(tmp_path))
    (u,) = result.results
    assert u.name == "U"
    assert u.field_type == NodalFieldType.DISP
    assert u.components == ["U1", "U2", "U3", "UR1", "UR2", "UR3"]
    # node id, then the six components; node 2's twist about X survives.
    np.testing.assert_allclose(u.values[1], [2, 0, 0, 0, 1, 0, 0])
    assert u.eigen_freq == 10.0
    assert u.step == 1  # an all-modal result is numbered by mode


def test_without_ur_u_is_translation_only(tmp_path):
    (u,) = read_results_sqlite(_db(tmp_path, with_ur=False)).results
    assert u.components == ["U1", "U2", "U3"]
