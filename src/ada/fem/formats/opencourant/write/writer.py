"""Write an OpenCourant (OpenRadioss) starter + engine deck pair from an adapy Assembly.

``<name>_0000.rad`` is the starter (model) deck and ``<name>_0001.rad`` the engine
(run control) deck. Version 1 covers explicit shell models: 3/4-node shells, elastic
or Johnson-Cook plastic metals, fixed BCs, initial velocities and shell-to-shell
contact (``/INTER/TYPE7``). Anything else raises ``NotImplementedError`` rather than
being silently dropped.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from ada.config import logger
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.shapes.definitions import ShellShapes
from ada.fem.steps import StepExplicit

from .cards import SEP, comment, f20, i10, int_rows, title

if TYPE_CHECKING:
    from ada import FEM, Assembly, Material
    from ada.fem import (
        Bc,
        Elem,
        FemSection,
        FemSet,
        Interaction,
        PredefinedField,
        Surface,
    )

#: Input-deck format version. The bundled keyword configuration knows 2026.
DECK_VERSION = 2026
#: Default number of animation states when the step leaves ``output_interval`` unset.
DEFAULT_ANIM_FRAMES = 50
#: Integration points through the shell thickness.
SHELL_NIP = 5
#: Johnson-Cook hardening exponent used when only yield and tensile strength are known.
JC_HARDENING_EXPONENT = 0.3
#: Plastic strain at which the Johnson-Cook curve reaches the tensile strength.
JC_UTS_PLASTIC_STRAIN = 0.15


def to_fem(assembly: Assembly, name, analysis_dir, metadata=None, model_data_only=False):
    """Write ``<name>_0000.rad`` (starter) and ``<name>_0001.rad`` (engine) into ``analysis_dir``."""
    analysis_dir = pathlib.Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    part = get_fem_model_from_assembly(assembly)
    deck = _StarterDeck(name, part.fem, assembly.fem)
    starter_path = analysis_dir / f"{name}_0000.rad"
    starter_path.write_text(deck.render())

    if model_data_only:
        return starter_path

    steps = [s for s in assembly.fem.steps + part.fem.steps if isinstance(s, StepExplicit)]
    if len(steps) == 0:
        # A model without an analysis (e.g. ``ada convert --to opencourant``) is still a valid
        # starter deck; the engine deck only exists to run a step.
        logger.info("No StepExplicit on the model: wrote the OpenCourant starter deck only")
        return starter_path
    if len(steps) > 1:
        logger.warning(f"OpenCourant writes one run; only the first explicit step {steps[0].name!r} is used")
    (analysis_dir / f"{name}_0001.rad").write_text(engine_deck_str(name, steps[0]))
    return starter_path


def engine_deck_str(name: str, step: StepExplicit) -> str:
    if step.total_time is None:
        raise ValueError(f"explicit step {step.name!r} has no total_time")
    t_end = float(step.total_time)
    interval = step.output_interval or t_end / DEFAULT_ANIM_FRAMES
    lines = [
        f"/RUN/{name}/1",
        f20(t_end),
        f"/VERS/{DECK_VERSION}",
        "/TFILE/0",
        f20(t_end / 500.0),
        "/ANIM/DT",
        f20(0.0) + f20(interval),
        "/ANIM/VECT/DISP",
        "/ANIM/VECT/VEL",
        "/ANIM/VECT/CONT",
        "/ANIM/ELEM/VONM",
        "/ANIM/ELEM/EPSP",
        "/ANIM/ELEM/ENER",
        "/ANIM/SHELL/THIC",
        "/PRINT/-100",
    ]
    if step.target_dt is not None:
        lines += ["/DT/NODA/CST/0", f20(0.9) + f20(step.target_dt)]
    return "\n".join(lines) + "\n/END\n"


@dataclass
class _Ids:
    """Monotonic id allocation per Radioss entity family."""

    counters: dict[str, int] = field(default_factory=dict)

    def next(self, family: str) -> int:
        self.counters[family] = self.counters.get(family, 0) + 1
        return self.counters[family]


class _StarterDeck:
    def __init__(self, name: str, fem: FEM, assembly_fem: FEM):
        self.name = name
        self.fem = fem
        self.afem = assembly_fem
        self.ids = _Ids()
        self.blocks: list[str] = []
        self._mat_ids: dict[str, int] = {}
        self._part_of_section: dict[str, int] = {}
        self._grnod_cache: dict[str, int] = {}
        self._surf_cache: dict[str, int] = {}

    # ---- entry -----------------------------------------------------------------------
    def render(self) -> str:
        self._begin()
        self._nodes()
        self._parts_and_elements()
        self._boundary_conditions()
        self._initial_velocities()
        self._interactions()
        self.blocks.append("/END\n")
        return "\n".join(self.blocks)

    # ---- model -----------------------------------------------------------------------
    def _begin(self):
        self.blocks.append(
            "\n".join(
                [
                    "#RADIOSS STARTER",
                    comment(" written by adapy (ada.fem.formats.opencourant)"),
                    "/BEGIN",
                    title(self.name),
                    i10(DECK_VERSION) + i10(0),
                    f"{'kg':>20s}{'m':>20s}{'s':>20s}",
                    f"{'kg':>20s}{'m':>20s}{'s':>20s}",
                    SEP,
                ]
            )
        )

    def _nodes(self):
        rows = ["/NODE"]
        for n in self.fem.nodes:
            rows.append(f"{i10(n.id)}{f20(n.x)}{f20(n.y)}{f20(n.z)}")
        self.blocks.append("\n".join(rows))

    def _parts_and_elements(self):
        for sec in self.fem.sections:
            sec_type = str(getattr(sec.type, "value", sec.type)).lower()
            if sec_type != "shell":
                raise NotImplementedError(f"OpenCourant writer v1 supports shell sections only (got {sec_type!r})")
            elements = list(sec.elset.members)
            if not elements:
                continue
            mat_id = self._material(sec.material)
            prop_id = self.ids.next("prop")
            self.blocks.append(_shell_prop_str(prop_id, sec))
            part_id = self.ids.next("part")
            self._part_of_section[sec.name] = part_id
            self.blocks.append(
                "\n".join(
                    [
                        f"/PART/{part_id}",
                        title(sec.name),
                        comment("  prop_ID    mat_ID subset_ID"),
                        i10(prop_id) + i10(mat_id) + i10(0),
                    ]
                )
            )
            self.blocks.append(_shell_elements_str(part_id, elements))

    def _material(self, mat: Material) -> int:
        if mat.name in self._mat_ids:
            return self._mat_ids[mat.name]
        mat_id = self.ids.next("mat")
        self._mat_ids[mat.name] = mat_id
        self.blocks.append(_material_str(mat_id, mat))
        return mat_id

    # ---- sets ------------------------------------------------------------------------
    def _grnod(self, key: str, label: str, node_ids: Iterable[int]) -> int:
        if key in self._grnod_cache:
            return self._grnod_cache[key]
        gid = self.ids.next("grnod")
        self._grnod_cache[key] = gid
        ids = sorted(set(int(i) for i in node_ids))
        self.blocks.append("\n".join([f"/GRNOD/NODE/{gid}", title(label)] + int_rows(ids)))
        return gid

    def _grnod_from_set(self, fem_set: FemSet) -> int:
        return self._grnod(f"set:{fem_set.name}", fem_set.name, _node_ids_of_set(fem_set))

    def _surface(self, key: str, label: str, elements: Iterable[Elem]) -> int:
        if key in self._surf_cache:
            return self._surf_cache[key]
        sid = self.ids.next("surf")
        self._surf_cache[key] = sid
        rows = [f"/SURF/SEG/{sid}", title(label)]
        for el in elements:
            ns = [n.id for n in el.nodes]
            if len(ns) == 3:
                ns = ns + [ns[2]]
            if len(ns) != 4:
                raise NotImplementedError(
                    f"contact segments need 3/4-node shells (element {el.id} has {len(ns)} nodes)"
                )
            rows.append(i10(el.id) + "".join(i10(n) for n in ns))
        self.blocks.append("\n".join(rows))
        return sid

    # ---- loads, BCs, contact ---------------------------------------------------------
    def _all_bcs(self) -> list[Bc]:
        bcs = list(self.fem.bcs) + list(self.afem.bcs)
        for step in self.afem.steps + self.fem.steps:
            bcs += list(step.bcs.values())
        return bcs

    def _boundary_conditions(self):
        for bc in self._all_bcs():
            if any(m not in (None, 0, 0.0) for m in bc.magnitudes):
                raise NotImplementedError(f"BC {bc.name!r}: prescribed non-zero motion is not supported yet")
            mask = ["0"] * 6
            for dof in bc.dofs:
                mask[int(dof) - 1] = "1"
            gid = self._grnod_from_set(bc.fem_set)
            self.blocks.append(
                "\n".join(
                    [
                        f"/BCS/{self.ids.next('bcs')}",
                        title(bc.name),
                        comment("  Tra rot   skew_ID  grnod_ID"),
                        f"   {''.join(mask[:3])} {''.join(mask[3:])}{i10(0)}{i10(gid)}",
                    ]
                )
            )

    def _initial_velocities(self):
        from ada.fem import PredefinedField

        fields: list[PredefinedField] = list(self.fem.predefined_fields.values()) + list(
            self.afem.predefined_fields.values()
        )
        for pf in fields:
            if pf.type != PredefinedField.TYPES.VELOCITY:
                continue
            vel = [0.0, 0.0, 0.0]
            magnitudes = pf.magnitude if isinstance(pf.magnitude, (list, tuple)) else [pf.magnitude] * len(pf.dofs)
            for dof, mag in zip(pf.dofs, magnitudes):
                if int(dof) > 3:
                    raise NotImplementedError(f"{pf.name!r}: rotational initial velocity is not supported yet")
                vel[int(dof) - 1] = float(mag)
            gid = self._grnod_from_set(pf.fem_set)
            self.blocks.append(
                "\n".join(
                    [
                        f"/INIVEL/TRA/{self.ids.next('inivel')}",
                        title(pf.name),
                        comment("                 Vx                  Vy                  Vz   Gnod_id   Skew_id"),
                        f20(vel[0]) + f20(vel[1]) + f20(vel[2]) + i10(gid) + i10(0),
                    ]
                )
            )

    def _interactions(self):
        interactions: list[Interaction] = list(self.fem.interactions.values()) + list(self.afem.interactions.values())
        for step in self.afem.steps + self.fem.steps:
            interactions += list(step.interactions.values())
        for inter in interactions:
            friction = float(getattr(inter.interaction_property, "friction", 0.0) or 0.0)
            if inter.surf1 is None or inter.surf2 is None:
                # general contact: every shell against every shell (TYPE7 handles self-contact)
                elements = list(self.fem.elements.shell)
                nodes = {n.id for el in elements for n in el.nodes}
                gid = self._grnod("all-shell-nodes", f"{inter.name}_nodes", nodes)
                sid = self._surface("all-shells", f"{inter.name}_surf", elements)
                self._type7(inter.name, gid, sid, friction)
                continue
            # surface-to-surface: TYPE7 is one-way (secondary nodes vs main segments), so
            # write both directions for a symmetric contact.
            for main, secondary in ((inter.surf1, inter.surf2), (inter.surf2, inter.surf1)):
                main_els = _surface_elements(main)
                sec_nodes = {n.id for el in _surface_elements(secondary) for n in el.nodes}
                sid = self._surface(f"surf:{main.name}", main.name, main_els)
                gid = self._grnod(f"surfnodes:{secondary.name}", f"{secondary.name}_nodes", sec_nodes)
                self._type7(f"{inter.name}:{secondary.name}->{main.name}", gid, sid, friction)

    def _type7(self, label: str, grnod_id: int, surf_id: int, friction: float):
        iid = self.ids.next("inter")
        self.blocks.append(
            "\n".join(
                [
                    f"/INTER/TYPE7/{iid}",
                    title(label),
                    comment(
                        " grnod_id   surf_id      Istf      Ithe      Igap                Ibag      Idel     Icurv      Iadm"
                    ),
                    i10(grnod_id)
                    + i10(surf_id)
                    + i10(4)
                    + i10(0)
                    + i10(3)
                    + " " * 10
                    + i10(0)
                    + i10(0)
                    + i10(0)
                    + i10(0),
                    comment(
                        "          Fscalegap             Gap_max             Fpenmax                         Itied      Ists"
                    ),
                    f20(0) + f20(0) + f20(0) + " " * 20 + i10(0) + i10(0),
                    comment(
                        "              Stmin               Stmax   Percent_mesh_size               dtmin  Irem_gap   Irem_i2"
                    ),
                    f20(0) + f20(0) + f20(0) + f20(0) + i10(0) + i10(0),
                    comment(
                        "              Stfac                Fric              GAPmin              Tstart               Tstop"
                    ),
                    f20(1.0) + f20(friction) + f20(0) + f20(0) + f20(0),
                    comment(
                        "      IBC                        Inacti               VIS_S               VIS_F              Bumult"
                    ),
                    " " * 7 + "000" + " " * 20 + i10(6) + f20(0) + f20(0) + f20(0),
                    comment(
                        "    Ifric    Ifiltr               Xfreq     Iform   sens_ID   fct_IDF             AscaleF   fric_ID"
                    ),
                    i10(0) + i10(0) + f20(0) + i10(0) + i10(0) + i10(0) + f20(0) + i10(0),
                ]
            )
        )


# ---- free helpers ----------------------------------------------------------------------
def _node_ids_of_set(fem_set: FemSet) -> set[int]:
    if fem_set.type == "nset":
        return {n.id for n in fem_set.members}
    return {n.id for el in fem_set.members for n in el.nodes}


def _surface_elements(surface: Surface) -> list[Elem]:
    sets = surface.fem_set if isinstance(surface.fem_set, (list, tuple)) else [surface.fem_set]
    out = []
    for fs in sets:
        if fs.type != "elset":
            raise NotImplementedError(f"surface {surface.name!r}: OpenCourant contact needs element-based surfaces")
        out += list(fs.members)
    return out


def _shell_elements_str(part_id: int, elements: list[Elem]) -> str:
    quads = [el for el in elements if el.type == ShellShapes.QUAD]
    trias = [el for el in elements if el.type == ShellShapes.TRI]
    other = [el for el in elements if el.type not in (ShellShapes.QUAD, ShellShapes.TRI)]
    if other:
        raise NotImplementedError(f"part {part_id}: unsupported shell element type {other[0].type!r}")
    out = []
    if quads:
        out.append(f"/SHELL/{part_id}")
        out += [i10(el.id) + "".join(i10(n.id) for n in el.nodes) for el in quads]
    if trias:
        out.append(f"/SH3N/{part_id}")
        out += [i10(el.id) + "".join(i10(n.id) for n in el.nodes) for el in trias]
    return "\n".join(out)


def _shell_prop_str(prop_id: int, sec: FemSection) -> str:
    # Ishell=24 (QEPH: under-integrated with physical hourglass stabilisation), Ish3n=2 (C0 triangle,
    # large rotations), Iplas=1 (iterative plasticity projection), N points through the thickness.
    return "\n".join(
        [
            f"/PROP/SHELL/{prop_id}",
            title(sec.name),
            comment("   Ishell    Ismstr     Ish3n    Idrill    Ipinch                  P_Thick_Fail"),
            i10(24) + i10(0) + i10(2) + i10(0) + i10(0) + " " * 10 + f20(0),
            comment(
                "                 Hm                  Hf                  Hr                  Dm                  Dn"
            ),
            f20(0) + f20(0) + f20(0) + f20(0) + f20(0),
            comment(
                "        N                         Thick              Ashear              Ithick     Iplas      Ipos"
            ),
            i10(SHELL_NIP) + " " * 10 + f20(sec.thickness) + f20(0) + " " * 10 + i10(1) + i10(1) + i10(0),
        ]
    )


def _material_str(mat_id: int, mat: Material) -> str:
    m = mat.model
    head = [
        comment("        Init. dens.          Ref. dens."),
        f20(m.rho) + f20(0),
        comment("                  E                  Nu"),
        f20(m.E) + f20(m.v),
    ]
    sig_y = getattr(m, "sig_y", None)
    if not sig_y:
        return "\n".join([f"/MAT/ELAST/{mat_id}", title(mat.name)] + head)
    sig_u = getattr(m, "sig_u", None) or sig_y
    hardening = max(sig_u - sig_y, 0.0) / JC_UTS_PLASTIC_STRAIN**JC_HARDENING_EXPONENT
    return "\n".join(
        [f"/MAT/PLAS_JOHNS/{mat_id}", title(mat.name)]
        + head
        + [
            comment(
                "                  a                   b                   n             Eps_max              sigmax"
            ),
            f20(sig_y) + f20(hardening) + f20(JC_HARDENING_EXPONENT) + f20(0) + f20(0),
            comment("                  c                EPS0       Icc   Fsmooth               F_CUT"),
            f20(0) + f20(0) + i10(0) + i10(0) + f20(0),
            comment("                  m              T_melt               rhoCp                 T_i"),
            f20(0) + f20(0) + f20(0) + f20(0),
        ]
    )
