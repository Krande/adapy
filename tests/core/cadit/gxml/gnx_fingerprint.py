"""What an adapy model read from GeniE amounts to, as one comparable value.

Used to show that two reads are the same model -- a workspace whose ACIS body is binary against
its text twin, or adapy's binary write against its text write. Everything a GeniE read produces is
in it: beams (ends, section, material, up vector), plates (outline or the curved face, thickness),
sections, materials, masses, the concept loads with their load cases and combinations, the support
points, and with ``build_topology_store`` the neutral B-rep's entity counts. Geometry ids are
random per read and are left out; nothing else is rounded or normalised.
"""

from __future__ import annotations

import re

import numpy as np

import ada


def _nums(x) -> tuple[float, ...]:
    return tuple(float(v) for v in np.asarray(x, dtype=float).ravel())


def _no_ids(text: str) -> str:
    return re.sub(r"id='[^']*'", "id=", text)


def model_fingerprint(a: ada.Assembly) -> dict[str, object]:
    fp: dict[str, object] = {}
    for p in sorted(a.get_all_subparts(), key=lambda q: q.name):
        fp[f"part {p.name}"] = (len(p.beams), len(p.plates), len(p.masses))
        for bm in sorted(p.beams, key=lambda b: b.name):
            fp[f"beam {bm.name}"] = (_nums(bm.n1.p), _nums(bm.n2.p), bm.section.name, bm.material.name, _nums(bm.up))
        for pl in sorted(p.plates, key=lambda q: q.name):
            if isinstance(pl, ada.PlateCurved):
                fp[f"plate {pl.name}"] = ("PlateCurved", float(pl.t), pl.material.name, _no_ids(repr(pl.geom)))
            else:
                outline = (_nums(pl.poly.points3d), _nums(pl.poly.normal))
                fp[f"plate {pl.name}"] = ("Plate", float(pl.t), pl.material.name, outline)
        for s in sorted(p.sections, key=lambda s: s.name):
            fp[f"section {s.name}"] = (s.type, s.h, s.w_top, s.w_btn, s.t_w, s.t_ftop, s.t_fbtn, s.r, s.wt)
        for m in sorted(p.materials, key=lambda m: m.name):
            fp[f"material {m.name}"] = (m.model.E, m.model.rho, m.model.sig_y, m.model.v)
        for i, mass in enumerate(p.masses):
            fp[f"mass {i}"] = _no_ids(repr(mass))
        loads = p.concept_fem.loads
        for name, lc in loads.load_cases.items():
            fp[f"load case {name}"] = _no_ids(repr(lc))
        for name, combo in loads.load_case_combinations.items():
            fp[f"combination {name}"] = _no_ids(repr(combo))
        for name, sp in p.concept_fem.constraints.point_constraints.items():
            fp[f"support {name}"] = _no_ids(repr(sp))
    store = getattr(a, "_topology_store", None)
    if store is not None:
        fp["brep"] = store.summary()
    return fp
