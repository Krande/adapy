"""The compat suite: tests whose subject is a CAD KERNEL rather than adapy's behaviour.

WHY THESE LIVE APART. adapy talks to a kernel through one abstraction (`ada.cad`'s backend
facade), and everything else in the test tree is written against that abstraction and runs on
whichever kernel the environment carries. The tests here are the exceptions, and they are
exceptions on purpose:

* cross-kernel parity -- the same construction through both kernels, compared. Needs both.
* pythonocc as the SUBJECT -- a helper, a writer or an oracle that is that kernel's, with no
  facade verb to reach it through.

Kept out of the default suite because an env without pythonocc was never meant to run them, and
reporting them as skips there turns "covered by another job" into what looks like missing
coverage -- 117 of them, which is enough to stop reading the number at all.

Everything under this directory is marked `pyocc` automatically, so the marker cannot drift from
the directory: `pixi run test-compat` selects the mark, the default suite deselects it, and a
file moved in or out changes both at once.
"""

import pathlib

import pytest

_HERE = pathlib.Path(__file__).parent


def pytest_collection_modifyitems(config, items):
    # Scoped to THIS directory on purpose: pytest hands every conftest's hook the WHOLE
    # collection, not just the items beneath it, so an unscoped loop here marks the entire test
    # tree as pythonocc-only and deselects it.
    for item in items:
        try:
            item.path.relative_to(_HERE)
        except (AttributeError, ValueError):
            continue
        item.add_marker(pytest.mark.pyocc)
