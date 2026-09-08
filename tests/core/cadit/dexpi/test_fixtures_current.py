"""Anti-drift: the checked-in example fixtures equal a fresh generation.

``scripts/gen_dexpi_examples.py`` builds each example once, in Python, and writes it through both
writers. Hand-editing one of the checked-in files, or changing a writer without regenerating, would
leave the fixture and the generator disagreeing silently -- exactly the kind of drift
``test_ada_ext_header_matches_schema`` guards against for the STEP codegen header. This is the same
guard for the DEXPI fixtures: it loads the generator module by path (never imports it as a package,
so ``scripts/`` need not be on the path) and compares its output text against the files on disk.
"""

from __future__ import annotations

import importlib.util
import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[4]
_GENERATOR = _REPO / "scripts" / "gen_dexpi_examples.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_dexpi_examples", _GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_checked_in_fixture_matches_a_fresh_generation(example_files):
    """A byte-for-byte match, not a canonical one -- the generator's output *is* the fixture."""
    dexpi_files = example_files / "dexpi_files"
    generator = _load_generator()
    fresh = generator.rendered()

    assert fresh, "the generator produced no fixtures at all"

    for name, expected_text in fresh.items():
        actual = (dexpi_files / name).read_text(encoding="utf-8")
        assert actual == expected_text, (
            f"{name} is stale versus scripts/gen_dexpi_examples.py -- "
            "run `python scripts/gen_dexpi_examples.py` and commit the result"
        )


def test_no_other_generated_fixture_is_missing_from_disk(example_files):
    """The reverse direction: nothing the generator produces is absent from ``files/dexpi_files/``."""
    dexpi_files = example_files / "dexpi_files"
    generator = _load_generator()

    for name in generator.rendered():
        assert (dexpi_files / name).exists(), f"{name} is generated but not checked in"
