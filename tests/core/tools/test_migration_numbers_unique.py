"""REST migrations have unique numbers. The two historical 027 files stay, because renaming one would re-apply it."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "ada" / "comms" / "rest" / "migrations"
HISTORICAL_PAIR = {"027_audit_log_failure_key.sql", "027_audit_started_at.sql"}


def names() -> list[str]:
    return sorted(p.name for p in MIGRATIONS.glob("*.sql"))


def test_every_migration_is_named_by_the_convention():
    assert [n for n in names() if not re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", n)] == []


def test_no_number_is_used_twice_except_the_historical_pair():
    by_number: dict[str, set[str]] = defaultdict(set)
    for name in names():
        by_number[name[:3]].add(name)
    repeated = {number: files for number, files in by_number.items() if len(files) > 1 and files != HISTORICAL_PAIR}
    next_free = max(int(number) for number in by_number) + 1
    assert repeated == {}, f"use {next_free:03d} for the new migration"


def test_the_exception_has_not_gone_stale():
    assert HISTORICAL_PAIR <= set(names())
