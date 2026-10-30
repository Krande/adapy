"""Element labels stay unique across the part instances of an ODB.

Abaqus numbers elements per part instance, so two instances can both have an element 1. The bake
keys every element by its label (the viewer's per-element draw ranges, element fields, beam
solids), and a repeated label made one instance overwrite the other: its elements could not be
selected.
"""

import sqlite3

import numpy as np

from ada.fem.formats.abaqus.results.sqlite_stream import ResultsSqliteStreamReader
from ada.fem.results.sqlite_schema import create_schema


def _db(tmp_path, second_label=1):
    """Two instances, one S4R shell each; the second's element is ``second_label``."""
    path = tmp_path / "parts.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.executemany("INSERT INTO ModelInstances (ID, Name) VALUES (?, ?)", [(1, "DECK-1"), (2, "BOOM-1")])
    for inst, (label, x0) in {1: (1, 0.0), 2: (second_label, 5.0)}.items():
        corners = [(x0, 0), (x0 + 1, 0), (x0 + 1, 1), (x0, 1)]
        conn.executemany(
            "INSERT INTO Points (InstanceID, ID, X, Y, Z) VALUES (?, ?, ?, ?, 0)",
            [(inst, i + 1, x, y) for i, (x, y) in enumerate(corners)],
        )
        conn.executemany(
            "INSERT INTO ElementConnectivity (InstanceID, ElemID, PointID, Seq) VALUES (?, ?, ?, ?)",
            [(inst, label, i + 1, i) for i in range(4)],
        )
        conn.execute(
            "INSERT INTO ElementInfo (InstanceID, ElemID, Type, IntPoints) VALUES (?, ?, 'S4R', 1)", (inst, label)
        )
    conn.commit()
    conn.close()
    return path


def _labels(reader):
    return sorted(int(x) for block in reader.read_mesh_geometry().cell_blocks for x in block.identifiers)


def test_a_repeated_label_moves_the_later_instance_into_its_own_block(tmp_path):
    reader = ResultsSqliteStreamReader(_db(tmp_path))
    assert _labels(reader) == [1, 11]  # DECK-1's element 1 keeps its number; BOOM-1's is 10 + 1
    assert reader.read_mesh_geometry().element_instances == [
        {"name": "DECK-1", "offset": 0},
        {"name": "BOOM-1", "offset": 10},
    ]


def test_unique_labels_are_left_alone(tmp_path):
    reader = ResultsSqliteStreamReader(_db(tmp_path, second_label=2))
    assert _labels(reader) == [1, 2]
    assert reader.read_mesh_geometry().element_instances is None
