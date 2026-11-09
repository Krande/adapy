"""A CalculiX ``.frd`` read case by case (``Assembly.to_fem(..., lazy=True)``, ``ada.from_fem_res(path, lazy=True)``):
its steps as cases, each as the role fields of :class:`~ada.fem.results.field_data.FieldRole`.

* ``NODAL_DISPLACEMENT`` from the ``DISP`` block (``D1 .. D6``; three components on a node of solids, padded with
  zeros to the role's six);
* ``NODAL_REACTION`` from ``FORC`` -- written only where the step asks for ``RF`` (``*NODE FILE``, RF) -- the
  external force at each node, which at a supported node with no load on it is the reaction.

The ``.frd`` holds six significant digits (``%12.5E``); ccx's U1 beams report their RF as the elements' end forces,
not reactions, and are not what this reads reactions off. The file is read whole (``read_frd_file``), then served one
case at a time.
"""

from __future__ import annotations

import pathlib
from typing import Iterator

import numpy as np


def open_frd_result(frd_file, **_options):
    from ada.fem.results.cases import ResultCases
    from ada.fem.results.common import FEAResult, FEATypes

    from .read_frd_file import read_from_frd_file_proto

    frd = pathlib.Path(frd_file)
    eager = read_from_frd_file_proto(frd)
    steps = sorted({int(r.step) for r in eager.results if r.name.split("[")[0] in ("DISP", "FORC")})

    def loader(cases, roles, elements, complex_phase) -> Iterator:
        from ada.fem.results.field_data import FieldRole, NodalFieldData, NodalFieldType

        if complex_phase:
            raise ValueError("CalculiX results hold no complex cases")
        want = set(FieldRole) if roles is None else set(roles)
        for case in cases:
            fields = []
            for name, role, ftype in (
                ("DISP", FieldRole.NODAL_DISPLACEMENT, NodalFieldType.DISP),
                ("FORC", FieldRole.NODAL_REACTION, NodalFieldType.FORCE),
            ):
                if role not in want:
                    continue
                src = [r for r in eager.results if r.name.split("[")[0] == name and int(r.step) == int(case)]
                if not src:
                    continue
                v = np.asarray(src[0].values, dtype=float)
                rows = np.zeros((len(v), 7))
                rows[:, 0] = v[:, 0]
                n = min(6, v.shape[1] - 1)
                rows[:, 1 : 1 + n] = v[:, 1 : 1 + n]
                fields.append(
                    NodalFieldData(
                        role.value,
                        int(case),
                        ["U1", "U2", "U3", "U4", "U5", "U6"],
                        rows,
                        field_type=ftype,
                        role=role,
                    )
                )
            yield int(case), FEAResult(frd.stem, FEATypes.CALCULIX, fields, None)

    eager.cases = ResultCases(stored=steps, loader=loader, names={s: f"step_{s}" for s in steps})
    return eager
