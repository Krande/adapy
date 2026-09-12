"""Small helpers shared by the query modules."""

from __future__ import annotations

import json


def _loads_jsonb(v):
    """asyncpg may hand JSONB back as a str (no codec registered) or already
    parsed. Normalize to a dict/list/None; never raise on a malformed row."""
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None
