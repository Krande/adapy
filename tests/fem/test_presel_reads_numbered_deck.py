"""Presel reads a deck adapy numbered, and rejects one whose name and IDENT disagree.

The check this branch was waiting for. The IDENT layout was taken from Presel's own files; this
is Presel itself, V8.0-01, run in line mode on decks adapy wrote. Measured on 2026-09-26:

* ``adaT10.FEM`` with ``IDENT 1.0 10.0 3.0`` and ``READ 10``: ``READING PASS 1..3``, then
  ``5 NODES READ / 4 BASIC ELEMENTS READ / 1 LOAD CASES READ`` -- superelement 10 is in.
* the same deck's ``IDENT`` saying 1, renamed ``misT10.FEM``, and ``READ 10``::

      **    NOT CORRESONDING SUPER ELEMENT TYPE
      **    ABORTED READING OF SUPER ELEMENT
            SUPER ELEMENT IS DELETED

  (Presel's own spelling.) So the number in the file name and ``SELTYP`` are one statement, and
  before this branch every deck adapy called ``...T10.FEM`` said 1 inside and was thrown out --
  or, worse, was named ``...T1.FEM`` and assembled as superelement 1 whatever the model was.

Presel 8.0 also logs ``UNKNOWN IDENTIFIER: UNITS`` and ``CARD NOT INTERPRETED: TDLOAD``; both
are records it does not know and skips, and the read completes. Sestra accepts both.

Skips where Presel is not installed: a pairing rule nothing has checked is not green.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

import ada
from ada.fem import Bc, FemSet, Load
from ada.fem.steps import StepImplicitStatic

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/presel_numbered"

#: What Presel logs when the number in the file name is not the deck's SELTYP. Its spelling.
REJECTED = "NOT CORRESONDING SUPER ELEMENT TYPE"


def _presel_exe():
    from ada.fem.formats.sesam.sesam_exe_locator import get_presel_default_exe_path

    try:
        return get_presel_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_presel_exe() is None, reason="Presel is not installed")


def _cantilever() -> ada.Assembly:
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300")
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.5, "line")
    root = p.fem.add_set(FemSet("root", [n for n in p.fem.nodes if abs(n.x) < 1e-9], "nset"))
    tip = p.fem.add_set(FemSet("tip", [n for n in p.fem.nodes if abs(n.x - 2.0) < 1e-9], "nset"))
    p.fem.add_bc(Bc("fix", root, [1, 2, 3, 4, 5, 6]))
    step = a.fem.add_step(StepImplicitStatic("lc", nl_geom=False, init_incr=1, total_time=1, max_incr=1))
    step.add_load(Load("F", Load.TYPES.FORCE, -1.0e4, fem_set=tip, dof=[0, 0, 1, 0, 0, 0]))
    return a


def _write_deck(number: int, name: str) -> pathlib.Path:
    """``<name>T<number>.FEM`` via the Sesam writer, its IDENT carrying ``number``."""
    a = _cantilever()
    a.to_fem(
        name,
        fem_format="sesam",
        scratch_dir=SCRATCH_DIR,
        overwrite=True,
        write_input_files_only=True,
        metadata={"sesam_superelement": number},
    )
    deck = SCRATCH_DIR / name / f"{name}T{number}.FEM"
    assert (
        deck.is_file()
    ), f"the writer should have named the deck after its number; {name} holds {list((SCRATCH_DIR / name).iterdir())}"
    return deck


def _presel_read(deck_dir: pathlib.Path, prefix: str, number: int) -> str:
    """Run ``READ <number>`` in line mode on ``<prefix>T<number>.FEM`` and return the journal.

    Presel logs what it did -- including its ``**`` errors -- on ``<prefix><name>.JNL``; its
    process exit code is 0 either way, so the journal is the only thing worth reading.
    """
    exe = _presel_exe()
    jnl = deck_dir / "read.jnl"
    jnl.write_text(f"READ {number} SHOW-PROGRESS\nEXIT\n")
    run_name = f"run{number}"
    for stale in deck_dir.glob(f"{prefix}{run_name}.*"):
        stale.unlink()
    subprocess.run(
        [
            exe,
            "/INTERFACE=LINE",
            f"/PREFIX={prefix}",
            f"/NAME={run_name}",
            "/STATUS=NEW",
            "/NOHEADER",
            f"/COMMAND-FILE={jnl.name}",
            "/FORCED-EXIT",
        ],
        cwd=str(deck_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    journal = deck_dir / f"{prefix}{run_name}.JNL"
    assert journal.is_file(), f"Presel wrote no journal in {deck_dir}: {sorted(os.listdir(deck_dir))}"
    return journal.read_text(errors="replace")


def test_presel_reads_the_deck_under_the_number_adapy_gave_it():
    deck = _write_deck(10, "ada")
    ident = next(line for line in deck.read_text().splitlines() if line.startswith("IDENT"))
    assert float(ident.split()[2]) == 10.0, f"precondition: the deck's own SELTYP is 10; IDENT was {ident!r}"

    journal = _presel_read(deck.parent, "ada", 10)

    assert REJECTED not in journal, journal
    assert "NODES READ" in journal and "BASIC ELEMENTS READ" in journal, journal
    assert "1 LOAD CASES READ" in journal, journal


def test_presel_rejects_a_deck_whose_name_and_ident_disagree():
    """The failure mode this branch removes, produced on purpose: a T10 file that says 1 inside."""
    deck = _write_deck(1, "one")
    mismatched = deck.parent / "misT10.FEM"
    shutil.copy(deck, mismatched)

    journal = _presel_read(deck.parent, "mis", 10)

    assert REJECTED in journal, journal
    assert "NODES READ" not in journal, journal
