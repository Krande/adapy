"""The FEA results SQLite schema: ``resources/results.sql``.

The Abaqus ODB exporter writes it and the results readers read it. Public: an exporter for any
solver that writes this schema is read by the same readers.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

SCHEMA_PATH = pathlib.Path(__file__).parent / "resources" / "results.sql"


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _declared_version(sql: str) -> int:
    match = re.search(r"^PRAGMA user_version\s*=\s*(\d+);", sql, re.M)
    if match is None:
        raise ValueError(f"{SCHEMA_PATH.name} declares no PRAGMA user_version")
    return int(match[1])


#: The schema version this adapy writes and reads; stamped into each database as user_version.
SCHEMA_VERSION = _declared_version(schema_sql())


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table (idempotent) and stamp the schema version."""
    conn.executescript(schema_sql())


def schema_version(conn: sqlite3.Connection) -> int:
    """The database's schema version. 0: written before the schema was versioned (identical to 1)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def check_schema_version(conn: sqlite3.Connection, source: object = "database") -> int:
    """Raise if the database is newer than this adapy can read; return its version."""
    version = schema_version(conn)
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"{source} uses FEA results schema v{version}; this adapy reads up to v{SCHEMA_VERSION}. Upgrade adapy."
        )
    return version


#: Abaqus component order: vector components 1-3, then symmetric-tensor ones 11, 22, 33, 12, 13, 23.
_COMPONENT_RANK = {"1": 0, "2": 1, "3": 2, "11": 0, "22": 1, "33": 2, "12": 3, "13": 4, "23": 5}


def order_components(field_vars: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """``(FieldID, name)`` pairs of one field in component order (U1, U2, U3; S11, S22, S33, S12, ...).

    FieldIDs are handed out as names are first met, and a history output (say the tip's U2) is
    met before the field output, so FieldID order is not component order.
    """

    def key(item: tuple[int, str]) -> tuple:
        fid, name = item
        head = name.rstrip("0123456789")
        return (head, _COMPONENT_RANK.get(name[len(head) :], 99), fid)

    return sorted(field_vars, key=key)


def is_results_db(conn: sqlite3.Connection) -> bool:
    """True if the database has the FEA results tables (as opposed to e.g. an IFC SQLite)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {"Points", "ElementInfo", "FieldVars", "FieldNodes"} <= tables
