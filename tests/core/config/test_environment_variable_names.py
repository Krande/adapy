"""A direct environment read must not use a name that a Config entry also answers to."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ada.config import Config, ConfigSection

SRC = Path(__file__).resolve().parents[3] / "src"
#: Read at call time as an override of the same setting, falling back to its Config entry when unset.
OVERRIDES_ITS_OWN_ENTRY = {"ADA_OCC_TESS_RELATIVE"}
READ = re.compile(r"""(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*["'](ADA_[A-Z0-9_]+)["']""")


def config_variables() -> set[str]:
    names = set()
    for item in Config._config_map:
        if isinstance(item, ConfigSection):
            names |= {entry.env_var(item.name) for entry in item.entries}
        else:
            names.add(item.env_var())
    return names


def test_no_direct_read_shadows_a_config_entry():
    if not SRC.is_dir():
        pytest.skip("src/ is not available")
    taken = config_variables()
    clashes = {}
    for path in SRC.rglob("*.py"):
        for name in READ.findall(path.read_text(encoding="utf-8", errors="replace")):
            if name in taken and name not in OVERRIDES_ITS_OWN_ENTRY:
                clashes.setdefault(name, []).append(str(path.relative_to(SRC)))
    assert clashes == {}
