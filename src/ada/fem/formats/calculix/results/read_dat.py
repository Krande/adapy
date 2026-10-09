"""Totals CalculiX prints to the .dat file."""

from __future__ import annotations

import os
import pathlib
import re

import numpy as np

_STEP = re.compile(r"^\s*S T E P\s+(\d+)\s*$")
_TOTAL = re.compile(r"^\s*total force \(fx,fy,fz\) for set (\S+) and time\s+(\S+)\s*$")


def read_reaction_totals(dat_path: str | os.PathLike) -> dict[tuple[int, str], np.ndarray]:
    """``{(step, SET): (fx, fy, fz)}``: the sums ``*NODE PRINT, TOTALS=ONLY`` / ``RF`` print, step by step (the
    last increment's wins), in ccx 2.23's format::

                            S T E P       1
         total force (fx,fy,fz) for set X0_SET and time  0.1000000E+03
                2.125470E-05  3.000881E-05  9.999992E+02

    Set names as ccx prints them (upper case). The sum is the set's external force: reactions plus loads at its nodes.
    """
    out: dict[tuple[int, str], np.ndarray] = {}
    step = 0
    lines = pathlib.Path(dat_path).read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        m = _STEP.match(line)
        if m:
            step = int(m.group(1))
            continue
        m = _TOTAL.match(line)
        if m is None:
            continue
        for nxt in lines[i + 1 :]:
            if nxt.strip():
                out[(step, m.group(1))] = np.array([float(v) for v in nxt.split()[:3]])
                break
    return out
