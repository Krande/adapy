import os
from collections.abc import Iterable
from itertools import chain, groupby
from operator import attrgetter
from typing import Union

from ada.concepts.levels import Assembly, Part
from ada.core.utils import NewLine
from ada.fem import (
    Amplitude,
    Connector,
    ConnectorSection,
    Constraint,
    Csys,
    Elem,
    FemSet,
    Interaction,
    InteractionProperty,
    Mass,
    PredefinedField,
    Spring,
    Surface,
)
from ada.fem.interactions import ContactTypes
from ada.fem.steps import (
    Step,
    StepEigen,
    StepEigenComplex,
    StepExplicit,
    StepImplicit,
    StepSteadyState,
)
from ada.fem.utils import convert_ecc_to_mpc, convert_hinges_2_couplings
from ada.materials import Material

from .common import get_instance_name
from .write_output_requests import predefined_field_str
from .write_sections import section_str
from .write_sets import aba_set_str
from .write_steps import abaqus_step_str
from .write_surfaces import surface_str

__all__ = ["to_fem"]

log_fin = "Please check your result and input. This is not a validated method of solving this issue"


_step_types = Union[StepEigen, StepImplicit, StepExplicit, StepSteadyState, StepEigenComplex]


def to_fem(assembly: Assembly, name, analysis_dir=None, metadata=None):
    a = AbaqusWriter(assembly)
    a.write(name, analysis_dir)
    print(f'Created an Abaqus input deck at "{a.analysis_path}"')


class AbaqusWriter:
    _subr_path = None
    _subroutine = None
    _imperfections = str()
    _node_hist_out = ["UT", "VT", "AT"]
    _con_hist_out = ["CTF", "CVF", "CP", "CU"]
    _rf_node_out = ["RT"]
    analysis_path = None
    parts_and_assemblies = True

    def __init__(self, assembly: Assembly):
        self.assembly = assembly

    def write(self, name, analysis_dir):
        """Build the Abaqus Analysis folder"""
        print("creating: {0}".format(name))

        self.analysis_path = analysis_dir

        for part in self.assembly.get_all_subparts():
            if len(part.fem.elements) + len(part.fem.connectors) == 0:
                continue
            if self.assembly.convert_options.hinges_to_coupling is True:
                convert_hinges_2_couplings(part.fem)

            if self.assembly.convert_options.ecc_to_mpc is True:
                convert_ecc_to_mpc(part.fem)

            self.write_part_bulk(part)

        core_dir = self.analysis_path / r"core_input_files"
        os.makedirs(core_dir)

        # Main Input File
        with open(self.analysis_path / f"{name}.inp", "w") as d:
            d.write(self.main_inp_str)

        # Connector Sections
        with open(core_dir / "connector_sections.inp", "w") as d:
            d.write(self.connector_sections_str)

        # Connectors
        with open(core_dir / "connectors.inp", "w") as d:
            d.write(self.connectors_str if len(self.assembly.fem.connectors) > 0 else "**")

        # Constraints
        with open(core_dir / "constraints.inp", "w") as d:
            d.write(self.constraints_str if len(self.assembly.fem.constraints) > 0 else "**")

        # Assembly data
        with open(core_dir / "assembly_data.inp", "w") as d:
            if len(self.assembly.fem.nodes) > 0:
                assembly_nodes_str = (
                    "*Node\n"
                    + "".join(
                        [
                            f"{no.id:>7}, {no.x:>13.6f}, {no.y:>13.6f}, {no.z:>13.6f}\n"
                            for no in sorted(self.assembly.fem.nodes, key=attrgetter("id"))
                        ]
                    ).rstrip()
                )
            else:
                assembly_nodes_str = "** No Nodes"
            d.write(f"{assembly_nodes_str}\n{self.nsets_str}\n{self.elsets_str}\n{self.surfaces_str}\n")
            d.write(orientations_str(self.assembly, self))

        # Amplitude data
        with open(core_dir / "amplitude_data.inp", "w") as d:
            d.write(self.amplitude_str)

        # Interaction Properties
        with open(core_dir / "interaction_prop.inp", "w") as d:
            d.write(self.int_prop_str)

        # Interactions data
        self.eval_interactions()
        with open(core_dir / "interactions.inp", "a") as d:
            d.write(self.predefined_fields_str)

        # Materials data
        with open(core_dir / "materials.inp", "w") as d:
            d.write(self.materials_str)

        # Boundary Condition data
        with open(core_dir / "bc_data.inp", "w") as d:
            d.write(self.bc_str)

        # Analysis steps
        for step_in in self.assembly.fem.steps:
            self.write_step(step_in)

    def eval_interactions(self):
        if len(self.assembly.fem.steps) > 0:
            initial_step = self.assembly.fem.steps[0]
            if type(initial_step) is StepExplicit:
                for interact in self.assembly.fem.interactions.values():
                    if interact.name not in initial_step.interactions.keys():
                        initial_step.add_interaction(interact)
                        return
        with open(self.analysis_path / "core_input_files/interactions.inp", "w") as d:
            if self.interact_str != "":
                d.write(self.interact_str)
                d.write("\n")

    def write_step(self, step_in: _step_types):
        step_str = abaqus_step_str(step_in)
        with open(self.analysis_path / "core_input_files" / f"step_{step_in.name}.inp", "w") as d:
            d.write(step_str)
            if "*End Step" not in step_str:
                d.write("*End Step\n")

    def write_part_bulk(self, part_in: Part):
        bulk_path = self.analysis_path / f"bulk_{part_in.name}"
        bulk_file = bulk_path / "aba_bulk.inp"
        os.makedirs(bulk_path, exist_ok=True)

        if part_in.fem.initial_state is not None:
            with open(bulk_file, "w") as d:
                d.write("** This part is replaced by an initial state step")
        else:
            fempart = AbaqusPartWriter(part_in)
            with open(bulk_file, "w") as d:
                d.write(fempart.bulk_str)

    def inst_inp_str(self, part: Part) -> str:
        if part.fem.initial_state is not None:
            import shutil

            istep = part.fem.initial_state
            analysis_name = os.path.basename(istep.initial_state_file.replace(".inp", ""))
            source_dir = os.path.dirname(istep.initial_state_file)
            for f in os.listdir(source_dir):
                if analysis_name in f:
                    dest_file = os.path.join(self.analysis_path, os.path.basename(f))
                    shutil.copy(os.path.join(source_dir, f), dest_file)
            return f"""*Instance, library={analysis_name}, instance={istep.initial_state_part.fem.instance_name}
**
** PREDEFINED FIELD
**
** Name: {part.fem.initial_state.name}   Type: Initial State
*Import, state=yes, update=no
*End Instance"""
        else:
            return f"""**\n*Instance, name={part.fem.instance_name}, part={part.name}\n*End Instance"""

    @property
    def constraint_control(self):
        constraint_ctrl_on = True
        for step in self.assembly.fem.steps:
            if type(step) == StepExplicit:
                constraint_ctrl_on = False
        return "**" if constraint_ctrl_on is False else "*constraint controls, print=yes"

    @property
    def main_inp_str(self):
        """Main input file for Abaqus analysis"""
        from .templates import main_inp_str

        def skip_if_this(p):
            if p.fem.initial_state is not None:
                return False
            return len(p.fem.elements) + len(p.fem.connectors) != 0

        def inst_skip(p):
            if p.fem.initial_state is not None:
                return True
            return len(p.fem.elements) + len(p.fem.connectors) != 0

        part_str = "\n".join(map(part_inp_str, filter(skip_if_this, self.assembly.get_all_subparts())))
        instance_str = "\n".join(map(self.inst_inp_str, filter(inst_skip, self.assembly.get_all_subparts())))
        step_str = (
            "\n".join(list(map(main_step_inp_str, self.assembly.fem.steps))).rstrip()
            if len(self.assembly.fem.steps) > 0
            else "** No Steps added"
        )
        incl = "*INCLUDE,INPUT=core_input_files"
        ampl_str = f"\n{incl}\\amplitude_data.inp" if self.amplitude_str != "" else "**"
        consec_str = f"\n{incl}\\connector_sections.inp" if self.connector_sections_str != "" else "**"
        int_prop_str = f"{incl}\\interaction_prop.inp" if self.int_prop_str != "" else "**"
        if self.interact_str != "" or self.predefined_fields_str != "":
            interact_str = f"{incl}\\interactions.inp"
        else:
            interact_str = "**"
        mat_str = f"{incl}\\materials.inp" if self.materials_str != "" else "**"
        fix_str = f"{incl}\\bc_data.inp" if self.bc_str != "" else "**"

        return main_inp_str.format(
            part_str=part_str,
            instance_str=instance_str.rstrip(),
            mat_str=mat_str,
            fix_str=fix_str,
            step_str=step_str,
            ampl_str=ampl_str,
            consec_str=consec_str,
            int_prop_str=int_prop_str,
            interact_str=interact_str,
            constr_ctrl=self.constraint_control,
        )

    @property
    def elsets_str(self):
        return (
            "\n".join([aba_set_str(el, True) for el in self.assembly.fem.elsets.values()]).rstrip()
            if len(self.assembly.fem.elsets) > 0
            else "** No element sets"
        )

    @property
    def nsets_str(self):
        return (
            "\n".join([aba_set_str(no, True) for no in self.assembly.fem.nsets.values()]).rstrip()
            if len(self.assembly.fem.nsets) > 0
            else "** No node sets"
        )

    @property
    def materials_str(self):
        return "\n".join([material_str(mat) for mat in self.assembly.materials])

    @property
    def surfaces_str(self):
        return (
            "\n".join([surface_str(s, True) for s in self.assembly.fem.surfaces.values()])
            if len(self.assembly.fem.surfaces) > 0
            else "** No Surfaces"
        )

    @property
    def constraints_str(self):
        return (
            "\n".join([AbaConstraint(c, True).str for c in self.assembly.fem.constraints])
            if len(self.assembly.fem.constraints) > 0
            else "** No Constraints"
        )

    @property
    def connector_sections_str(self):
        return "\n".join([connector_section_str(consec) for consec in self.assembly.fem.connector_sections.values()])

    @property
    def connectors_str(self):
        return "\n".join([connector_str(con, self) for con in self.assembly.fem.connectors.values()])

    @property
    def amplitude_str(self):
        return "\n".join([amplitude_str(ampl) for ampl in self.assembly.fem.amplitudes.values()])

    @property
    def interact_str(self):
        return "\n".join([interaction_str(interact, self) for interact in self.assembly.fem.interactions.values()])

    @property
    def int_prop_str(self):
        iprop_str = "\n".join([interaction_prop_str(iprop) for iprop in self.assembly.fem.intprops.values()])
        smoothings = self.assembly.fem.metadata.get("surf_smoothing", None)
        if smoothings is not None:
            iprop_str += "\n"
            for smooth in smoothings:
                name = smooth["name"]
                iprop_str += f"*Surface Smoothing, name={name}\n"
                iprop_str += smooth["bulk"] + "\n"
        return iprop_str

    @property
    def predefined_fields_str(self):
        def eval_fields(pre_field: PredefinedField):
            return True if pre_field.type != PredefinedField.TYPES.INITIAL_STATE else False

        return "\n".join(
            [
                predefined_field_str(prefield)
                for prefield in filter(eval_fields, self.assembly.fem.predefined_fields.values())
            ]
        )

    @property
    def bc_str(self):
        from .write_bc import bc_str

        return "\n".join(
            chain.from_iterable(
                (
                    [bc_str(bc, True) for bc in self.assembly.fem.bcs],
                    [bc_str(bc, True) for p in self.assembly.get_all_parts_in_assembly() for bc in p.fem.bcs],
                )
            )
        )

    def __repr__(self):
        return "AbaqusWriter()"


class AbaqusPartWriter:
    def __init__(self, part: Part):
        self.part = part

    @property
    def bulk_str(self):
        return f"""** Abaqus Part {self.part.name}
** Exported using ADA OpenSim
*NODE
{self.nodes_str}
{self.elements_str}
{self.elsets_str}
{self.nsets_str}
{self.sections_str}
{self.mass_str}
{self.surfaces_str}
{self.constraints_str}
{self.springs_str}""".rstrip()

    @property
    def sections_str(self):
        return section_str(self.part.fem)

    @property
    def elsets_str(self):
        if len(self.part.fem.elsets) > 0:
            return "\n".join([aba_set_str(el, False) for el in self.part.fem.elsets.values()]).rstrip()
        else:
            return "** No element sets"

    @property
    def nsets_str(self):
        if len(self.part.fem.nsets) > 0:
            return "\n".join([aba_set_str(no, False) for no in self.part.fem.nsets.values()]).rstrip()
        else:
            return "** No node sets"

    @property
    def nodes_str(self):
        f = "{nid:>7}, {x:>13.6f}, {y:>13.6f}, {z:>13.6f}"
        return (
            "\n".join(
                [
                    f.format(nid=no.id, x=no[0], y=no[1], z=no[2])
                    for no in sorted(self.part.fem.nodes, key=attrgetter("id"))
                ]
            ).rstrip()
            if len(self.part.fem.nodes) > 0
            else "** No Nodes"
        )

    @property
    def elements_str(self):
        part_el = self.part.fem.elements
        grouping = groupby(part_el, key=attrgetter("type", "elset"))
        return (
            "".join([els for els in [elwriter(x, elements) for x, elements in grouping] if els is not None]).rstrip()
            if len(self.part.fem.elements) > 0
            else "** No elements"
        )

    @property
    def mass_str(self):
        return (
            "\n".join([mass_str(m) for m in self.part.fem.masses.values()])
            if len(self.part.fem.masses) > 0
            else "** No Masses"
        )

    @property
    def surfaces_str(self):
        if len(self.part.fem.surfaces) > 0:
            return "\n".join([surface_str(s, False) for s in self.part.fem.surfaces.values()])
        else:
            return "** No Surfaces"

    @property
    def constraints_str(self):
        return (
            "\n".join([AbaConstraint(c, False).str for c in self.part.fem.constraints])
            if len(self.part.fem.constraints) > 0
            else "** No Constraints"
        )

    @property
    def springs_str(self):
        return (
            "\n".join([spring_str(c) for c in self.part.fem.springs.values()])
            if len(self.part.fem.springs) > 0
            else "** No Springs"
        )

    @property
    def instance_move_str(self):
        if self.part.fem.metadata["move"] is not None:
            move = self.part.fem.metadata["move"]
            mo_str = "\n " + ", ".join([str(x) for x in move])
        else:
            mo_str = "\n 0.,        0.,           0."

        if self.part.fem.metadata["rotate"] is not None:
            rotate = self.part.fem.metadata["rotate"]
            vecs = ", ".join([str(x) for x in rotate[0]])
            vece = ", ".join([str(x) for x in rotate[1]])
            angle = rotate[2]
            move_str = """{move_str}\n {vecs}, {vece}, {angle}""".format(
                move_str=mo_str, vecs=vecs, vece=vece, angle=angle
            )
        else:
            move_str = "" if mo_str == "0.,        0.,           0." else mo_str
        return move_str


class AbaConstraint:
    """

    Coupling definition:
    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-coupling.htm#simakey-r-coupling

    """

    def __init__(self, constraint: Constraint, on_assembly_level: bool):
        self.constraint = constraint
        self._on_assembly_level = on_assembly_level

    @property
    def _coupling(self):
        dofs_str = "".join(
            [f" {x[0]}, {x[1]}\n" if type(x) != int else f" {x}, {x}\n" for x in self.constraint.dofs]
        ).rstrip()

        if type(self.constraint.s_set) is FemSet:
            new_surf = surface_str(
                Surface(
                    f"{self.constraint.name}_surf",
                    Surface.TYPES.NODE,
                    self.constraint.s_set,
                    1.0,
                    parent=self.constraint.s_set.parent,
                ),
                self._on_assembly_level,
            )
            surface_ref = f"{self.constraint.name}_surf"
            add_str = new_surf
        else:
            add_str = "**"
            surface_ref = get_instance_name(self.constraint.s_set, self._on_assembly_level)

        if self.constraint.csys is not None:
            new_csys_str = "\n" + csys_str(self.constraint.csys, self._on_assembly_level)
            cstr = f", Orientation={self.constraint.csys.name.upper()}"
        else:
            cstr = ""
            new_csys_str = ""

        rnode = f"{get_instance_name(self.constraint.m_set.members[0], self._on_assembly_level)}"
        return f"""** ----------------------------------------------------------------
** Coupling element {self.constraint.name}
** ----------------------------------------------------------------{new_csys_str}
** COUPLING {self.constraint.name}
{add_str}
*COUPLING, CONSTRAINT NAME={self.constraint.name}, REF NODE={rnode}, SURFACE={surface_ref}{cstr}
*KINEMATIC
{dofs_str}""".rstrip()

    @property
    def _mpc(self):
        mpc_type = self.constraint.mpc_type
        m_members = self.constraint.m_set.members
        s_members = self.constraint.s_set.members
        mpc_vars = "\n".join([f" {mpc_type},{m.id:>8},{s.id:>8}" for m, s in zip(m_members, s_members)])
        return f"** Constraint: {self.constraint.name}\n*MPC\n{mpc_vars}"

    @property
    def _shell2solid(self):
        mname = self.constraint.m_set.name
        sname = self.constraint.s_set.name
        influence = self.constraint.influence_distance
        influence_str = "" if influence is None else f", influence distance={influence}"
        return (
            f"** Constraint: {self.constraint.name}\n*Shell to Solid Coupling, "
            f"constraint name={self.constraint.name}{influence_str}\n{mname}, {sname}"
        )

    @property
    def str(self):
        if self.constraint.type == Constraint.TYPES.COUPLING:
            return self._coupling
        elif self.constraint.type == Constraint.TYPES.TIE:
            return _tie(self.constraint)
        elif self.constraint.type == Constraint.TYPES.RIGID_BODY:
            rnode = get_instance_name(self.constraint.m_set, True)
            return f"*Rigid Body, ref node={rnode}, elset={get_instance_name(self.constraint.s_set, True)}"
        elif self.constraint.type == Constraint.TYPES.MPC:
            return self._mpc
        elif self.constraint.type == Constraint.TYPES.SHELL2SOLID:
            return self._shell2solid
        else:
            raise NotImplementedError(f"{self.constraint.type}")


def main_step_inp_str(step: _step_types) -> str:
    return f"""*INCLUDE,INPUT=core_input_files\\step_{step.name}.inp"""


def part_inp_str(part: Part) -> str:
    return """**\n*Part, name={name}\n*INCLUDE,INPUT=bulk_{name}\\{inp_file}\n*End Part\n**""".format(
        name=part.name, inp_file="aba_bulk.inp"
    )


def _tie(constraint: Constraint) -> str:
    num = 80
    pos_tol_str = ""
    if constraint.pos_tol is not None:
        pos_tol_str = f", position tolerance={constraint.pos_tol},"

    coupl_text = "**" + num * "-" + """\n** COUPLING {}\n""".format(constraint.name) + "**" + num * "-" + "\n"
    name = constraint.name

    adjust = constraint.metadata.get("adjust", "no")

    coupl_text += f"""** Constraint: {name}
*Tie, name={name}, adjust={adjust}{pos_tol_str}
{constraint.m_set.name}, {constraint.s_set.name}"""
    return coupl_text


def aba_write(el: Elem):
    nl = NewLine(10, suffix=7 * " ")
    if len(el.nodes) > 6:
        di = " {}"
    else:
        di = "{:>13}"
    return f"{el.id:>7}, " + " ".join([f"{di.format(no.id)}," + next(nl) for no in el.nodes])[:-1]


def elwriter(eltype_set, elements):
    if "connector" in eltype_set:
        return None
    eltype, elset = eltype_set
    el_set_str = f", ELSET={elset.name}" if elset is not None else ""
    el_str = "\n".join(map(aba_write, elements))
    return f"""*ELEMENT, type={eltype}{el_set_str}\n{el_str}\n"""


def interaction_str(interaction: Interaction, fem_writer) -> str:
    # Allowing Free text to be parsed directly through interaction class.
    if "aba_bulk" in interaction.metadata.keys():
        return interaction.metadata["aba_bulk"]

    contact_mod = interaction.metadata["contact_mod"] if "contact_mod" in interaction.metadata.keys() else "NEW"
    contact_incl = (
        interaction.metadata["contact_inclusions"]
        if "contact_inclusions" in interaction.metadata.keys()
        else "ALL EXTERIOR"
    )

    top_str = f"**\n** Interaction: {interaction.name}"
    if interaction.type == ContactTypes.SURFACE:
        adjust_par = interaction.metadata.get("adjust", None)
        geometric_correction = interaction.metadata.get("geometric_correction", None)
        small_sliding = interaction.metadata.get("small_sliding", None)

        first_line = "" if small_sliding is None else f", {small_sliding}"

        if issubclass(type(interaction.parent), Step):
            step = interaction.parent
            first_line += "" if type(step) is StepExplicit else f", type={interaction.surface_type}"
        else:
            first_line += f", type={interaction.surface_type}"

        if interaction.constraint is not None:
            first_line += f", mechanical constraint={interaction.constraint}"

        if adjust_par is not None:
            first_line += f", adjust={adjust_par}" if adjust_par is not None else ""

        if geometric_correction is not None:
            first_line += f", geometric correction={geometric_correction}"

        return f"""{top_str}
*Contact Pair, interaction={interaction.interaction_property.name}{first_line}
{get_instance_name(interaction.surf1, fem_writer)}, {get_instance_name(interaction.surf2, fem_writer)}"""
    else:
        return f"""{top_str}\n*Contact, op={contact_mod}
*Contact Inclusions, {contact_incl}
*Contact Property Assignment
 ,  , {interaction.interaction_property.name}"""


def material_str(material: Material) -> str:
    if "aba_inp" in material.metadata.keys():
        return material.metadata["aba_inp"]
    if "rayleigh_damping" in material.metadata.keys():
        alpha, beta = material.metadata["rayleigh_damping"]
    else:
        alpha, beta = None, None

    no_compression = material.metadata["no_compression"] if "no_compression" in material.metadata.keys() else False
    compr_str = "\n*No Compression" if no_compression is True else ""

    if material.model.eps_p is not None and len(material.model.eps_p) != 0:
        pl_str = "\n*Plastic\n"
        pl_str += "\n".join(
            ["{x:>12.5E}, {y:>10}".format(x=x, y=y) for x, y in zip(material.model.sig_p, material.model.eps_p)]
        )
    else:
        pl_str = ""

    if alpha is not None and beta is not None:
        d_str = "\n*Damping, alpha={alpha}, beta={beta}".format(alpha=material.model.alpha, beta=material.model.beta)
    else:
        d_str = ""

    if material.model.zeta is not None and material.model.zeta != 0.0:
        exp_str = "\n*Expansion\n {zeta}".format(zeta=material.model.zeta)
    else:
        exp_str = ""

    # Density == 0.0 is unsupported
    density = material.model.rho if material.model.rho > 0.0 else 1e-6

    return f"""*Material, name={material.name}
*Elastic
{material.model.E:.6E},  {material.model.v}{compr_str}
*Density
{density},{exp_str}{d_str}{pl_str}"""


def amplitude_str(amplitude: Amplitude) -> str:
    name, x, y, smooth = amplitude.name, amplitude.x, amplitude.y, amplitude.smooth
    a = 1
    data = ""
    for i, var in enumerate(zip(list(x), list(y))):
        if a == 4:
            if i == len(list(x)) - 1:
                data += "{:.4E}, {:.4E}, ".format(var[0], var[1])
            else:
                data += "{:.4E}, {:.4E},\n         ".format(var[0], var[1])
            a = 0
        else:
            data += "{:.4E}, {:.4E}, ".format(var[0], var[1])
        a += 1

    smooth = ", DEFINITION=TABULAR, SMOOTH={}".format(smooth) if smooth is not None else ""
    amplitude = """*Amplitude, name={0}{2}\n         {1}\n""".format(name, data, smooth)
    return amplitude.rstrip()


def connector_str(connector: Connector, fem_writer) -> str:
    csys_ref = "" if connector.csys is None else f'\n "{connector.csys.name}",'

    end1 = get_instance_name(connector.n1, fem_writer)
    end2 = get_instance_name(connector.n2, fem_writer)
    return f"""**
** ----------------------------------------------------------------
** Connector element representing {connector.name}
** ----------------------------------------------------------------
**
*Elset, elset={connector.name}
 {connector.id},
*Element, type=CONN3D2
 {connector.id}, {end1}, {end2}
*Connector Section, elset={connector.name}, behavior={connector.con_sec.name}
 {connector.con_type},{csys_ref}
*Elset, elset={connector.name}_set
 {connector.id}
**
{csys_str(connector.csys, fem_writer)}
**"""


def connector_section_str(con_sec: ConnectorSection):
    """

    :param con_sec:
    :type con_sec: ada.fem.ConnectorSection
    :return:
    """

    conn_txt = """*Connector Behavior, name={0}""".format(con_sec.name)
    elast = con_sec.elastic_comp
    damping = con_sec.damping_comp
    plastic_comp = con_sec.plastic_comp
    rigid_dofs = con_sec.rigid_dofs
    soft_elastic_dofs = con_sec.soft_elastic_dofs
    if type(elast) is float:
        conn_txt += """\n*Connector Elasticity, component=1\n{0:.3E},""".format(elast)
    else:
        for i, comp in enumerate(elast):
            if isinstance(comp, Iterable) is False:
                conn_txt += """\n*Connector Elasticity, component={1} \n{0:.3E},""".format(comp, i + 1)
            else:
                conn_txt += f"\n*Connector Elasticity, nonlinear, component={i + 1}, DEPENDENCIES=1"
                for val in comp:
                    conn_txt += "\n" + ", ".join([f"{x:>12.3E}" if u <= 1 else f",{x:>12d}" for u, x in enumerate(val)])

    if type(damping) is float:
        conn_txt += """\n*Connector Damping, component=1\n{0:.3E},""".format(damping)
    else:
        for i, comp in enumerate(damping):
            if type(comp) is float:
                conn_txt += """\n*Connector Damping, component={1} \n{0:.3E},""".format(comp, i + 1)
            else:
                conn_txt += """\n*Connector Damping, nonlinear, component=1, DEPENDENCIES=1"""
                for val in comp:
                    conn_txt += "\n" + ", ".join(
                        ["{:>12.3E}".format(x) if u <= 1 else ",{:>12d}".format(x) for u, x in enumerate(val)]
                    )

    # Optional Choices
    if plastic_comp is not None:
        for i, comp in enumerate(plastic_comp):
            conn_txt += """\n*Connector Plasticity, component={}\n*Connector Hardening, definition=TABULAR""".format(
                i + 1
            )
            for val in comp:
                force, motion, rate = val
                conn_txt += "\n{}, {}, {}".format(force, motion, rate)

    if rigid_dofs is not None:
        conn_txt += "\n*Connector Elasticity, rigid\n "
        conn_txt += ", ".join(["{0}".format(x) for x in rigid_dofs])

    if soft_elastic_dofs is not None:
        for dof in soft_elastic_dofs:
            conn_txt += "\n*Connector Elasticity, component={0}\n 5.0,\n".format(dof)

    return conn_txt


def interaction_prop_str(int_prop: InteractionProperty):
    """

    :param int_prop:
    :type int_prop: ada.fem.InteractionProperty
    :return:
    """
    iprop_str = f"*Surface Interaction, name={int_prop.name}\n"

    # Friction
    iprop_str += f"*Friction\n{int_prop.friction},\n"

    # Behaviours
    tab_str = (
        "\n" + "\n".join(["{:>12.3E},{:>12.3E}".format(d[0], d[1]) for d in int_prop.tabular])
        if int_prop.tabular is not None
        else ""
    )
    iprop_str += f"*Surface Behavior, pressure-overclosure={int_prop.pressure_overclosure}{tab_str}"

    return iprop_str.rstrip()


def mass_str(mass: Mass) -> str:
    type_str = f", type={mass.point_mass_type}" if mass.point_mass_type is not None else ""
    mstr = ",".join([str(x) for x in mass.mass]) if type(mass.mass) is list else str(mass.mass)

    if mass.type == Mass.TYPES.MASS:
        return f"""*Mass, elset={mass.fem_set.name}{type_str}\n {mstr}"""
    elif mass.type == Mass.TYPES.NONSTRU:
        return f"""*Nonstructural Mass, elset={mass.fem_set.name}, units={mass.units}\n  {mstr}"""
    elif mass.type == Mass.TYPES.ROT_INERTIA:
        return f"""*Rotary Inertia, elset={mass.fem_set.name}\n  {mstr}"""
    else:
        raise ValueError(f'Mass type "{mass.type}" is not supported by Abaqus')


def orientations_str(assembly: Assembly, fem_writer) -> str:
    """Add orientations associated with loads"""
    cstr = "** Orientations associated with Loads"
    for step in assembly.fem.steps:
        for load in step.loads:
            if load.csys is None:
                continue
            cstr += "\n"
            coord_str = ", ".join([str(x) for x in chain.from_iterable(load.csys.coords)])[:-1]
            name = load.fem_set.name.upper()
            inst_name = get_instance_name(load.fem_set, fem_writer)
            cstr += f"*Nset, nset=_T-{name}, internal\n{inst_name},\n"
            cstr += f"*Transform, nset=_T-{name}\n{coord_str}\n"
            cstr += csys_str(load.csys, fem_writer)

    return cstr.strip()


def csys_str(csys: Csys, fem_writer):
    name = csys.name.replace('"', "").upper()
    ori_str = f'*Orientation, name="{name}"'
    if csys.nodes is None and csys.coords is None:
        ori_str += "\n 1.,           0.,           0.,           0.,           1.,           0.\n 1, 0."
    elif csys.nodes is not None:
        if len(csys.nodes) != 3:
            raise ValueError("CSYS number of nodes must be 3")
        ori_str += ", SYSTEM=RECTANGULAR, DEFINITION=NODES\n {},{},{}".format(
            *[get_instance_name(no, fem_writer) for no in csys.nodes]
        )
    else:
        ax, ay, az = csys.coords[0]
        ori_str += f" \n{ax}, {ay}, {az}"
        bx, by, bz = csys.coords[1]
        ori_str += f", {bx}, {by}, {bz}"
        if len(csys.coords) == 3:
            cx, cy, cz = csys.coords[2]
            ori_str += f", {cx}, {cy}, {cz}"
        ori_str += "\n 1, 0."
    return ori_str


def spring_str(spring: Spring) -> str:
    from ada.fem.shapes import ElemShape

    if spring.type in ElemShape.TYPES.spring1n:
        _str = f'** Spring El "{spring.name}"\n\n'
        for dof, row in enumerate(spring.stiff):
            for j, stiffness in enumerate(row):
                if dof == j:
                    _str += f"""*Spring, elset={spring.fem_set.name}
 {dof + 1}
 {stiffness:.6E}
{spring.id}, {spring.nodes[0].id}\n"""
        return _str.rstrip()
    else:
        raise ValueError(f'Currently unsupported spring type "{spring.type}"')
