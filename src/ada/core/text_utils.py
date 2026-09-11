"""Small, dependency-free string helpers shared across layers (identifiers,
slugs). Stdlib-only by construction: this module ships in the slim viewer
runtime (see ``deploy/Dockerfile.viewer``) and is imported by CAD readers,
the procedural model and the REST layer alike, so nothing here may import
numpy or the modelling API.
"""

from __future__ import annotations

import re


def slugify(value: str) -> str:
    """A URL/identifier-safe slug: lowercase, non-alphanumerics collapsed to a
    single hyphen, trimmed. Empty input yields ``""`` (caller rejects)."""
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return s
