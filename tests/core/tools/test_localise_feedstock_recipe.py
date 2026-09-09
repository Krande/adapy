"""The feedstock recipe, pointed at a checkout.

Everything adapy's suite runs, it runs from a checkout — so nothing we test can see
what the PACKAGE contains. Two real failures came out of that blind spot: `ada-py`
shipping with zero migration `.sql` files, and the 0.67.0 feedstock build failing on
DEXPI tests that load a script the recipe does not ship. Both were found after the
fact, by something other than us.

The rewrite these tests cover is what lets CI build the feedstock's own recipe
against this checkout, so a packaging fault shows up before a release rather than
after one. It is textual on purpose — a YAML round-trip drops the recipe's comments,
and one of those records why a test is deselected — which is exactly why the shape
of what it edits is worth pinning.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tools"))

from localise_feedstock_recipe import (  # noqa: E402
    RecipeRewriteError,
    localise,
    project_version,
)

RECIPE = """schema_version: 1

context:
  name: ada-py
  version: "0.64.1"
  python_min: "3.11"

recipe:
  name: ${{ name }}
  version: ${{ version }}

source:
  url: https://github.com/Krande/adapy/archive/v${{ version }}.tar.gz
  sha256: 1088444dcb0c07895bf821c8c128c0f3f94bf60f587ec5f42121a1be3544c17c

build:
  number: 0

outputs:
  - package:
      name: ${{ name }}-core
    tests:
      - files:
          source:
            - tests
            - files
        script:
          # it is a repo lint, not a package test.
          - pytest tests --deselect tests/core/cad/test_tess_env_defaults.py::test_x
"""


def test_the_tarball_is_replaced_by_the_checkout():
    out = localise(RECIPE, "/work/adapy")
    assert "path: /work/adapy" in out
    # Neither half of the fetch may survive: a leftover `url` would be built INSTEAD
    # of the checkout on some rattler versions, which is the one failure mode that
    # would make this whole check silently test the released package again.
    source_block = out.split("build:", 1)[0]
    assert "url:" not in source_block
    assert "sha256:" not in source_block


def test_the_comments_survive():
    # The rewrite is textual precisely to keep these. The deselect comment is the
    # only record of WHY that test is excluded from the package build; a YAML
    # round-trip would drop it and the next person would delete the deselect.
    out = localise(RECIPE, "/work/adapy")
    assert "it is a repo lint, not a package test." in out


def test_the_version_can_be_overridden_and_only_the_context_one_moves():
    # Building a checkout ahead of the released version while still claiming the old
    # number is the confusion this tool exists to remove.
    out = localise(RECIPE, "/work/adapy", version="0.67.0")
    assert 'version: "0.67.0"' in out
    assert 'version: "0.64.1"' not in out
    # The outputs refer to it indirectly and must keep doing so, or the built
    # package's name stops tracking the context.
    assert "version: ${{ version }}" in out


def test_the_version_is_left_alone_when_not_asked_for():
    out = localise(RECIPE, "/work/adapy")
    assert 'version: "0.64.1"' in out


def test_the_rest_of_the_recipe_is_untouched():
    out = localise(RECIPE, "/work/adapy", version="0.67.0")
    for fragment in ("schema_version: 1", "name: ada-py", "python_min:", "- tests", "- files", "number: 0"):
        assert fragment in out, fragment


def test_a_recipe_with_no_source_is_refused_rather_than_silently_built():
    # Building the wrong thing is worse than not building: a recipe this cannot
    # localise would fetch the released tarball and report a pass that says nothing
    # about the checkout.
    with pytest.raises(RecipeRewriteError, match="no `source:` block"):
        localise("schema_version: 1\ncontext:\n  name: x\n", "/work/adapy")


def test_the_version_override_refuses_when_there_is_nothing_to_override():
    with pytest.raises(RecipeRewriteError, match="context.version"):
        localise("source:\n  url: x\n\nbuild:\n  number: 0\n", "/work/adapy", version="1.0.0")


def test_the_project_version_is_read_from_pyproject(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "ada-py"\nversion = "1.2.3"\n', encoding="utf-8")
    assert project_version(p) == "1.2.3"


def test_a_pyproject_without_a_version_is_an_error(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "ada-py"\n', encoding="utf-8")
    with pytest.raises(RecipeRewriteError):
        project_version(p)


def test_it_localises_the_real_feedstock_recipe_shape(tmp_path):
    """A guard against the recipe drifting away from what this can rewrite.

    The upstream recipe is not vendored here, so this uses the shape above — but the
    assertion that matters is that a rewrite leaves NO fetch behind, which is the
    thing that would make the check pass while testing the released package.
    """
    out = localise(RECIPE, str(tmp_path), version="9.9.9")
    assert out.count("path:") == 1
    assert "https://github.com/" not in out
