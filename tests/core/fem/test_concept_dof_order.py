"""The order a support's six dofs are listed and written in."""

from __future__ import annotations

import os
import subprocess
import sys


def test_dofs_are_listed_in_one_order_whatever_the_hash_seed():
    """The six dofs came out in a set's order, which changes with PYTHONHASHSEED, so the same
    script wrote a different XML from one run to the next."""
    code = (
        "import ada; "
        "sp = ada.ConstraintConceptPoint('p', (0, 0, 0), [ada.ConstraintConceptDofType('dz', 'free')]); "
        "print([d.dof for d in sp.dof_constraints], [d.dof for d in ada.ConstraintConceptDofType.encastre()])"
    )
    seen = set()
    for seed in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        seen.add(out.stdout.strip())
    expected = ["dx", "dy", "dz", "rx", "ry", "rz"]
    assert seen == {f"{expected} {expected}"}
