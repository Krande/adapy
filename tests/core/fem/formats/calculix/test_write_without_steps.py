"""The Calculix writer must accept a model that has no analysis step.

``to_fem`` used to do ``step_str(assembly.fem.steps[0])`` unconditionally, so any model
without a step raised ``IndexError``. That is the normal shape of a *converted* model --
``ada convert --to calculix`` carries geometry and a mesh, never an analysis -- and both the
Abaqus and the Sesam writers already tolerate it, so Calculix was the odd one out.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import LoadGravity, StepImplicitStatic


@pytest.fixture
def shell_beam() -> ada.Assembly:
    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    return ada.Assembly("MyAssembly") / (ada.Part("MyPart", fem=bm.to_fem_obj(0.1, "shell")) / bm)


def test_write_without_steps(shell_beam, tmp_path):
    shell_beam.to_fem("no_steps", fem_format="calculix", overwrite=True, scratch_dir=tmp_path)

    deck = tmp_path / "no_steps" / "no_steps.inp"
    assert deck.is_file()

    text = deck.read_text(errors="replace")
    assert "** No steps" in text
    assert "*STEP" not in text.upper()
    # The rest of the deck is still there -- the guard must not swallow the model.
    assert "*NODE" in text.upper()
    assert "*MATERIAL" in text.upper()


def test_write_with_a_step_still_emits_it(shell_beam, tmp_path):
    """The other side of the guard: a model that does have a step is unchanged."""
    step = StepImplicitStatic("static", total_time=1, max_incr=1, init_incr=1, nl_geom=True)
    step.add_load(LoadGravity("Gravity"))
    shell_beam.fem.add_step(step)

    shell_beam.to_fem("with_step", fem_format="calculix", overwrite=True, scratch_dir=tmp_path)

    text = (tmp_path / "with_step" / "with_step.inp").read_text(errors="replace")
    assert "** No steps" not in text
    assert "*STEP" in text.upper()
