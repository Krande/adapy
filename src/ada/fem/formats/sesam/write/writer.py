from __future__ import annotations

import datetime
from operator import attrgetter
from typing import TYPE_CHECKING

from ada.config import logger
from ada.core.utils import Counter, get_current_user
from ada.fem import FEM
from ada.fem.exceptions.model_definition import DoesNotSupportMultiPart

from .templates import top_level_fem_str
from .write_sets import sets_str
from .write_utils import write_ff

if TYPE_CHECKING:
    from ada import Material


def to_fem(assembly, name, analysis_dir=None, metadata=None, model_data_only=False):
    from .write_constraints import constraint_str
    from .write_elements import elem_str
    from .write_loads import loads_str
    from .write_masses import mass_str
    from .write_sections import sections_str
    from .write_steps import write_sestra_inp

    if metadata is None:
        metadata = dict()

    if "control_file" not in metadata.keys():
        metadata["control_file"] = None

    parts = list(filter(lambda x: len(x.fem.nodes) > 0, assembly.get_all_subparts(include_self=True)))
    if len(parts) != 1:
        raise DoesNotSupportMultiPart(
            f"Sesam writer currently only works for a single part. Currently found {len(parts)}"
        )

    if len(assembly.fem.steps) > 1:
        logger.error("Sesam writer currently only supports 1 step. Will only use 1st step")

    part = parts[0]

    thick_map = dict()

    now = datetime.datetime.now()
    date_str = now.strftime("%d-%b-%Y")
    clock_str = now.strftime("%H:%M:%S")
    user = get_current_user()

    units = "UNITS     5.00000000E+00  1.00000000E+00  1.00000000E+00  1.00000000E+00\n          1.00000000E+00\n"

    assembly.consolidate_sections()
    assembly.consolidate_materials()
    materials = assembly.get_all_materials(True)

    inp_file_path = (analysis_dir / f"{name}T1").with_suffix(".FEM")

    if len(assembly.fem.steps) > 0:
        step = assembly.fem.steps[0]
        with open(analysis_dir / "sestra.inp", "w") as f:
            f.write(write_sestra_inp(name, step))

    with open(inp_file_path, "w") as d:
        d.write(top_level_fem_str.format(date_str=date_str, clock_str=clock_str, user=user))
        d.write(units)
        d.write(materials_str(materials))
        d.write(sections_str(part.fem, thick_map))
        d.write(univec_str(part.fem))
        d.write(nodes_str(part.fem))
        d.write(mass_str(part.fem))
        d.write(sets_str(part.fem))
        d.write(bc_str(part.fem) + bc_str(assembly.fem))
        d.write(constraint_str(part.fem) + constraint_str(assembly.fem))
        d.write(hinges_str(part.fem))
        d.write(elem_str(part.fem, thick_map))
        d.write(loads_str(assembly.fem) + loads_str(part.fem))
        d.write("IEND                0.00            0.00            0.00            0.00\n")

    print(f'Created an Sesam input deck at "{analysis_dir}"')


def materials_str(materials: list[Material]):
    out_str = "".join([write_ff("TDMATER", [(4, mat.id, 100 + len(mat.name), 0), (mat.name,)]) for mat in materials])

    out_str += "".join(
        [
            write_ff(
                "MISOSEL",
                [
                    (mat.id, mat.model.E, mat.model.v, mat.model.rho),
                    (mat.model.zeta, mat.model.alpha, 1, mat.model.sig_y),
                ],
            )
            for mat in materials
        ]
    )
    return out_str


def nodes_str(fem: FEM) -> str:
    nodes = sorted(fem.nodes, key=attrgetter("id"))

    nids = []
    for n in nodes:
        if n.id not in nids:
            nids.append(n.id)
        else:
            raise Exception('Doubly defined node id "{}". TODO: Make necessary code updates'.format(n[0]))
    if len(nodes) == 0:
        return "** No Nodes"
    else:
        out_str = "".join([write_ff("GNODE", [(no.id, no.id, 6, 123456)]) for no in nodes])
        out_str += "".join([write_ff("GCOORD", [(no.id, no[0], no[1], no[2])]) for no in nodes])
        return out_str


def bc_str(fem: FEM) -> str:
    out_str = ""
    for bc in fem.bcs:
        for m in bc.fem_set.members:
            dofs = [1 if i in bc.dofs else 0 for i in range(1, 7)]
            data = [tuple([m.id, 6] + dofs[:2]), tuple(dofs[2:])]
            out_str += write_ff("BNBCD", data)
    return out_str


def hinges_str(fem: FEM) -> str:
    out_str = ""
    h = Counter(1)

    def write_hinge(hinge):
        dofs = [0 if i in hinge else 1 for i in range(1, 7)]
        fix_id = next(h)
        data = [tuple([fix_id, 3, 0, 0]), tuple(dofs[:4]), tuple(dofs[4:])]
        return fix_id, write_ff("BELFIX", data)

    for el in fem.elements:
        h1, h2 = el.metadata.get("h1", None), el.metadata.get("h2", None)
        if h2 is None and h1 is None:
            continue
        h1_fix, h2_fix = 0, 0
        if h1 is not None:
            h1_fix, res_str = write_hinge(h1)
            out_str += res_str
        if h2 is not None:
            h2_fix, res_str = write_hinge(h2)
            out_str += res_str
        el.metadata["fixno"] = h1_fix, h2_fix

    return out_str


def univec_str(fem: FEM) -> str:
    out_str = ""
    uvec_id = Counter(1)

    unit_vecs = dict()

    def write_local_z(vec):
        tvec = tuple(vec)
        if tvec in unit_vecs.keys():
            return unit_vecs[tvec], None
        trans_no = next(uvec_id)
        data = [tuple([trans_no, *vec])]
        unit_vecs[tvec] = trans_no
        return trans_no, write_ff("GUNIVEC", data)

    for el in fem.elements.stru_elements:
        local_z = el.fem_sec.local_z
        transno, res_str = write_local_z(local_z)
        if res_str is None:
            el.metadata["transno"] = transno
            continue
        out_str += res_str
        el.metadata["transno"] = transno

    return out_str
