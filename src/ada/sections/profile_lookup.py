"""Section catalog lookup for the component-spec form.

`load_profiles_by_category(category)` returns a sorted list of section
identifiers (e.g. ``["HEA100", "HEA120", ...]``) for a given
SectionCat category. Used by the viewer's
``GET /api/components/profiles?category=...`` endpoint to populate
section dropdowns in the configuration form.

ProfileDB.json only ships parametric data for a subset of categories
today (HEA/HEB/HEM/IPE/HP/UNP); categories without DB entries return
an empty list. The frontend should accept free-text input as a
fallback so users can still type custom sizes (e.g.
"BOX300x300x12x12") that adapy can parse via ``interpret_section_str``.
"""

from __future__ import annotations

import functools
import json
import pathlib

from ada.sections.categories import SectionCat


_PROFILE_DB_PATH = pathlib.Path(__file__).parent / "resources" / "ProfileDB.json"


@functools.lru_cache(maxsize=1)
def _load_profile_db() -> dict[str, dict]:
    with _PROFILE_DB_PATH.open() as fp:
        return json.load(fp).get("ProfileDB", {})


def list_categories() -> list[str]:
    """Return the names of all SectionCat categories."""
    return sorted(
        attr
        for attr in dir(SectionCat)
        if not attr.startswith("_") and isinstance(getattr(SectionCat, attr), list)
    )


def load_profiles_by_category(category: str) -> list[str]:
    """Return sorted section identifiers belonging to ``category``.

    Matches by the prefixes declared on the corresponding ``SectionCat``
    attribute against the top-level keys in ProfileDB.json
    (case-insensitive). Returns an empty list for unknown categories
    or categories without ProfileDB entries.
    """
    prefixes = getattr(SectionCat, category.lower().strip(), None)
    if not isinstance(prefixes, list):
        return []

    db = _load_profile_db()
    db_upper = {k.upper(): v for k, v in db.items()}

    out: list[str] = []
    for prefix in prefixes:
        bucket = db_upper.get(prefix.upper())
        if bucket:
            out.extend(bucket.keys())
    return sorted(out)
