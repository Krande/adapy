"""An ASSEMBLY element (a connector between parts) is drawn between its part instances' own nodes.

Abaqus numbers nodes per part instance, and a connector defined in the assembly joins nodes of two
of them. The ODB lists those nodes again under the assembly, keyed by label alone: two parts' node
2 collide there, and the copies carry no displacement. Read through the copies, a connector was
drawn to the wrong node and stood still while the model deformed. Schema v3 records each node's
instance (``ElementConnectivity.PointInstanceID``).
"""

import sqlite3

import numpy as np

from ada.fem.formats.abaqus.results.sqlite_stream import ResultsSqliteStreamReader
from ada.fem.results.sqlite_schema import create_schema

# Two parts, both with nodes 1 and 2, five apart in x.
PART_NODES = {1: {1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0)}, 2: {1: (5.0, 0.0, 0.0), 2: (6.0, 0.0, 0.0)}}


def _db(tmp_path, *, point_instances=True):
    """DECK-1 and BOOM-1 with a line element each, joined by an assembly CONN3D2 from DECK-1 node 2
    to BOOM-1 node 1. The assembly also lists the two parts' node 2 under the same label, the way an
    ODB export does."""
    path = tmp_path / "connected.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.executemany(
        "INSERT INTO ModelInstances (ID, Name) VALUES (?, ?)", [(0, "ASSEMBLY"), (1, "DECK-1"), (2, "BOOM-1")]
    )
    for inst, nodes in PART_NODES.items():
        conn.executemany(
            "INSERT INTO Points (InstanceID, ID, X, Y, Z) VALUES (?, ?, ?, ?, ?)",
            [(inst, label, *xyz) for label, xyz in nodes.items()],
        )
        conn.execute("INSERT INTO ElementInfo (InstanceID, ElemID, Type, IntPoints) VALUES (?, 1, 'B31', 1)", (inst,))
        conn.executemany(
            "INSERT INTO ElementConnectivity (InstanceID, ElemID, PointID, Seq) VALUES (?, 1, ?, ?)",
            [(inst, 1, 0), (inst, 2, 1)],
        )
    # The assembly's label-keyed copies: node 2 of both parts, and node 1 of BOOM-1.
    conn.executemany(
        "INSERT INTO Points (InstanceID, ID, X, Y, Z) VALUES (0, ?, ?, ?, ?)",
        [(2, *PART_NODES[1][2]), (2, *PART_NODES[2][2]), (1, *PART_NODES[2][1])],
    )
    conn.execute("INSERT INTO ElementInfo (InstanceID, ElemID, Type, IntPoints) VALUES (0, 900, 'CONN3D2', 1)")
    if point_instances:
        conn.executemany(
            "INSERT INTO ElementConnectivity (InstanceID, ElemID, PointID, Seq, PointInstanceID) VALUES (0, 900, ?, ?, ?)",
            [(2, 0, 1), (1, 1, 2)],
        )
    else:
        conn.executemany(
            "INSERT INTO ElementConnectivity (InstanceID, ElemID, PointID, Seq) VALUES (0, 900, ?, ?)", [(2, 0), (1, 1)]
        )
    conn.commit()
    conn.close()
    return path


def _connector_ends(reader):
    geom = reader.read_mesh_geometry()
    for block in geom.cell_blocks:
        for row, label in zip(block.data, block.identifiers):
            if int(label) == 900:
                return [tuple(geom.points[i]) for i in row], [int(i) for i in row]
    raise AssertionError("connector 900 not in the mesh")


def test_a_connector_runs_between_its_part_instances_nodes(tmp_path):
    reader = ResultsSqliteStreamReader(_db(tmp_path))
    ends, rows = _connector_ends(reader)
    assert ends == [PART_NODES[1][2], PART_NODES[2][1]]
    # The parts' own points (the ones their displacements land on), not the assembly's copies.
    offsets = reader._inst_point_offset
    assert rows[0] - offsets[1] in range(2) and rows[1] - offsets[2] in range(2)


def test_a_file_from_before_v3_reads_as_before(tmp_path):
    path = _db(tmp_path, point_instances=False)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE ElementConnectivity DROP COLUMN PointInstanceID")
    conn.commit()
    conn.close()
    ends, _ = _connector_ends(ResultsSqliteStreamReader(path))
    # No instance to go on: the assembly's own points, as v2 files have always been read.
    assert np.allclose(ends[1], PART_NODES[2][1])
