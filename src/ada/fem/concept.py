from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Dict, Iterable, List, Literal, Union

from ada.api.containers import Nodes
from ada.comms.fb_model_gen import FilePurposeDC
from ada.config import logger
from ada.visit.renderer_manager import FEARenderParams, RendererManager, RenderParams

from .containers import FemElements, FemSections, FemSets
from .sets import FemSet
from .surfaces import Surface

if TYPE_CHECKING:
    from ada import Part
    from ada.api.beams import Beam
    from ada.api.nodes import Node
    from ada.fem import (
        Amplitude,
        Bc,
        Connector,
        ConnectorSection,
        Constraint,
        Csys,
        Elem,
        FemSection,
        Interaction,
        InteractionProperty,
        Load,
        Mass,
        PredefinedField,
        Spring,
        StepEigen,
        StepExplicit,
        StepImplicitStatic,
        StepSteadyState,
    )
    from ada.fem.options import FemOptions
    from ada.fem.results.common import Mesh
    from ada.fem.steps import Step

_step_types = Union["StepSteadyState", "StepEigen", "StepImplicitStatic", "StepExplicit"]


@dataclass
class InterfaceNode:
    node: Node
    constraint: Constraint = field(default=None)
    connector: Connector = field(default=None)


@dataclass
class FEM:
    name: str
    metadata: Dict = field(default_factory=dict)
    parent: Part = field(init=True, default=None)

    masses: Dict[str, Mass] = field(init=False, default_factory=dict)
    surfaces: Dict[str, Surface] = field(init=False, default_factory=dict)
    amplitudes: Dict[str, Amplitude] = field(init=False, default_factory=dict)
    connector_sections: Dict[str, ConnectorSection] = field(init=False, default_factory=dict)
    springs: Dict[str, Spring] = field(init=False, default_factory=dict)
    intprops: Dict[str, InteractionProperty] = field(init=False, default_factory=dict)
    interactions: Dict[str, Interaction] = field(init=False, default_factory=dict)
    predefined_fields: Dict[str, PredefinedField] = field(init=False, default_factory=dict)
    lcsys: Dict[str, Csys] = field(init=False, default_factory=dict)
    constraints: Dict[str, Constraint] = field(init=False, default_factory=dict)

    bcs: List[Bc] = field(init=False, default_factory=list)
    steps: List[Union[StepSteadyState, StepEigen, StepImplicitStatic, StepExplicit]] = field(
        init=False, default_factory=list
    )

    nodes: Nodes = field(default_factory=Nodes, init=True)
    ref_points: Nodes = field(default_factory=Nodes, init=True)
    ref_sets: FemSets = field(default_factory=FemSets, init=True)

    elements: FemElements = field(default_factory=FemElements, init=True)
    sets: FemSets = field(default_factory=FemSets, init=True)
    sections: FemSections = field(default_factory=FemSections, init=True)
    initial_state: PredefinedField = field(default=None, init=True)
    subroutine: str = field(default=None, init=True)

    interface_nodes: List[Union[Node, InterfaceNode]] = field(init=False, default_factory=list)

    def __post_init__(self):
        self.nodes.parent = self
        self.elements.parent = self
        self.sets.parent = self
        self.sections.parent = self
        from ada.fem.options import FemOptions

        self._options = FemOptions()

    def add_elem(self, elem: Elem) -> Elem:
        elem.parent = self
        self.elements.add(elem)
        return elem

    def add_section(self, section: FemSection) -> FemSection:
        section.parent = self
        if section.elset.parent is None:
            if section.elset.name in self.elsets.keys():
                fs = self.elsets[section.elset.name]
            else:
                fs = self.sets.add(section.elset)
            if fs != section.elset:
                logger.info(f'Element set "{section.elset}" is replaced by {fs}')
                section.elset = fs
        if section.material.parent is None and self.parent is not None:
            self.parent.add_material(section.material)
        self.sections.add(section)
        return section

    def add_bc(self, bc: Bc) -> Bc:
        if bc.name in [b.name for b in self.bcs]:
            raise ValueError(f'BC with name "{bc.name}" already exists')

        bc.parent = self
        if bc.fem_set.parent is None:
            logger.debug("Bc FemSet has no parent. Adding to self")
            self.sets.add(bc.fem_set)

        self.bcs.append(bc)
        return bc

    def add_mass(self, mass: Mass) -> Mass:
        mass.parent = self
        self.elements.add(mass)
        elset = self.sets.add(FemSet(mass.name + "_set", [mass], "elset"))
        mass.elset = elset
        return mass

    def add_set(
        self,
        fem_set: FemSet,
        p=None,
        vol_box=None,
        vol_cyl=None,
        single_member=False,
        tol=1e-4,
    ) -> FemSet:
        """
        :param fem_set: A fem set object
        :param p: Single point (x,y,z)
        :param vol_box: Search by a box volume. Where p is (xmin, ymin, zmin) and vol_box is (xmax, ymax, zmax)
        :param vol_cyl: Search by cylindrical volume. Used together with p to find
                        nodes within cylinder inputted by [radius, height, thickness]
        :param single_member: Set True if you wish to keep only a single member
        :param tol: Point Tolerances. Default is 1e-4
        """
        fem_set.parent = self

        def append_members(nodelist):
            if single_member is True:
                fem_set.add_members([nodelist[0]])
            else:
                fem_set.add_members(nodelist)

        if fem_set.type != fem_set.TYPES.NSET or all(x is None for x in [p, vol_box, vol_cyl]):
            self.sets.add(fem_set)
            return fem_set

        nodes = self.nodes.get_by_volume(p, vol_box, vol_cyl, tol)
        if len(nodes) > 0:
            append_members(nodes)
            self.sets.add(fem_set)
            return fem_set

        if len(nodes) == 0 and self.parent is not None:
            assembly = self.parent.get_assembly()
            list_of_ps = assembly.get_all_subparts() + [assembly]
            for part in list_of_ps:
                nodes = part.fem.nodes.get_by_volume(p, vol_box, vol_cyl, tol)
                if len(nodes) == 0:
                    continue
                fem_set.parent = part.fem
                append_members(nodes)
                part.fem.add_set(fem_set)
                return fem_set

        raise Exception(f'No nodes found for femset "{fem_set.name}"')

    def add_step(self, step: _step_types) -> _step_types:
        """Add an analysis step to the assembly"""
        from ada.fem.steps import Step

        if len(self.steps) > 0:
            if self.steps[-1].type != Step.TYPES.EIGEN and step.type == Step.TYPES.COMPLEX_EIG:
                raise Exception("Complex eigenfrequency analysis step needs to follow eigenfrequency step.")
        step.parent = self
        for bc in step.bcs.values():
            if bc.amplitude is not None:
                if bc.amplitude.parent is None:
                    self.add_amplitude(bc.amplitude)
        self.steps.append(step)

        return step

    def add_interaction_property(self, int_prop: InteractionProperty) -> InteractionProperty:
        int_prop.parent = self
        self.intprops[int_prop.name] = int_prop
        return int_prop

    def add_interaction(self, interaction: Interaction) -> Interaction:
        interaction.parent = self
        self.interactions[interaction.name] = interaction
        if interaction.interaction_property.parent is None:
            self.add_interaction_property(interaction.interaction_property)
        return interaction

    def add_constraint(self, constraint: Constraint) -> Constraint:
        constraint.parent = self
        if constraint.m_set.parent is None:
            if isinstance(constraint.m_set, FemSet):
                self.add_set(constraint.m_set)
            elif isinstance(constraint.m_set, Surface):
                self.add_surface(constraint.m_set)
                if constraint.m_set.fem_set.parent is None:
                    self.add_set(constraint.m_set.fem_set)
            else:
                raise ValueError(f"Constraint m_set must be FemSet or Surface, not {type(constraint.m_set)}")

        if constraint.s_set.parent is None:
            if isinstance(constraint.s_set, FemSet):
                self.add_set(constraint.s_set)
            elif isinstance(constraint.s_set, Surface):
                self.add_surface(constraint.s_set)
                if constraint.s_set.fem_set.parent is None:
                    self.add_set(constraint.s_set.fem_set)
            else:
                raise ValueError(f"Constraint s_set must be FemSet or Surface, not {type(constraint.s_set)}")

        self.constraints[constraint.name] = constraint
        return constraint

    def add_lcsys(self, lcsys: Csys) -> Csys:
        if lcsys.name in self.lcsys.keys():
            raise ValueError("Local Coordinate system cannot have duplicate name")
        lcsys.parent = self
        self.lcsys[lcsys.name] = lcsys
        return lcsys

    def add_connector_section(self, connector_section: ConnectorSection) -> ConnectorSection:
        connector_section.parent = self
        self.connector_sections[connector_section.name] = connector_section
        return connector_section

    def add_connector(self, connector: Connector) -> Connector:
        from ada import Assembly

        connector.parent = self
        if not isinstance(self.parent, Assembly):
            logger.warning(
                "Connector Elements can usually only be added to an Assembly object. Please check your model."
            )

        self.elements.add(connector)
        connector.csys.parent = self
        if connector.con_sec.parent is None:
            self.add_connector_section(connector.con_sec)
        fs = self.add_set(FemSet(name=connector.name, members=[connector], set_type="elset"))
        connector.elset = fs
        return connector

    def add_rp(self, name: str, node: Node):
        """Adds a reference point in assembly with a specific name"""
        node.parent = self
        node_ = self.ref_points.add(node)
        fem_set = self.ref_sets.add(FemSet(name, [node_], "nset", parent=self))
        fem_set.metadata["internal"] = True
        return node_, fem_set

    def add_surface(self, surface: Surface) -> Surface:
        surface.parent = self
        self.surfaces[surface.name] = surface
        return surface

    def add_amplitude(self, amplitude: Amplitude) -> Amplitude:
        amplitude.parent = self
        self.amplitudes[amplitude.name] = amplitude
        return amplitude

    def add_predefined_field(self, pre_field: PredefinedField) -> PredefinedField:
        pre_field.parent = self
        self.predefined_fields[pre_field.name] = pre_field
        return pre_field

    def add_spring(self, spring: Spring) -> Spring:
        if spring.fem_set.parent is None:
            self.sets.add(spring.fem_set)
        self.springs[spring.name] = spring
        return spring

    def add_interface_nodes(self, interface_nodes: List[Union[Node, InterfaceNode]]):
        """Nodes used for interfacing between other parts. Pass a custom Constraint if specific coupling is needed"""
        from ada import Node

        for n in interface_nodes:
            n_in = InterfaceNode(n) if isinstance(n, Node) else n
            self.interface_nodes.append(n_in)

    def create_fem_elem_from_obj(self, obj, el_type=None) -> Elem:
        """Converts structural object to FEM elements. Currently only BEAM is supported"""
        from ada.fem.shapes import ElemType

        if type(obj) is not Beam:
            raise NotImplementedError(f'Object type "{type(obj)}" is not yet supported')

        el_type = ElemType.LINE if el_type is None else el_type

        res = self.nodes.add(obj.n1)
        if res is not None:
            obj.n1 = res
        res = self.nodes.add(obj.n2)
        if res is not None:
            obj.n2 = res

        elem = self.add_elem(Elem(None, [obj.n1, obj.n2], el_type))
        femset = self.add_set(FemSet(f"{obj.name}_set", [elem], FemSet.TYPES.ELSET))
        self.add_section(
            FemSection(
                f"d{obj.name}_sec",
                ElemType.LINE,
                femset,
                obj.material,
                obj.section,
                obj.ori[1],
            )
        )
        return elem

    def is_empty(self) -> bool:
        containers = [
            len(self.nodes),
            len(self.elements),
        ]

        for n_cont in containers:
            if n_cont != 0:
                return False

        return True

    def get_all_steps(self) -> list[Step]:
        assembly = self.parent.get_assembly()
        steps = []
        for p in assembly.get_all_parts_in_assembly(include_self=True):
            if len(p.fem.steps) == 0:
                continue
            steps += p.fem.steps
        return steps

    def get_all_bcs(self) -> Iterable[Bc]:
        """Get all the boundary conditions in the entire assembly"""
        assembly = self.parent.get_assembly()
        return chain.from_iterable(
            (
                assembly.fem.bcs,
                [bc for p in assembly.get_all_parts_in_assembly() for bc in p.fem.bcs],
            )
        )

    def get_all_masses(self) -> Iterable[Mass]:
        """Get all the Masses in the entire assembly"""
        assembly = self.parent.get_assembly()
        return chain.from_iterable(
            (
                assembly.fem.masses.values(),
                [mass for p in assembly.get_all_parts_in_assembly() for mass in p.fem.masses.values()],
            )
        )

    def get_all_loads(self) -> list[Load]:
        loads = []
        for step in self.steps:
            for load in step.loads:
                loads.append(load)
        return loads

    def show(
        self,
        renderer: Literal["react", "pygfx"] = "react",
        host="localhost",
        port=8765,
        server_exe: pathlib.Path = None,
        server_args: list[str] = None,
        run_ws_in_thread=False,
        unique_id=None,
        purpose: FilePurposeDC = FilePurposeDC.ANALYSIS,
        params_override: RenderParams = None,
        solid_beams=True,
        ping_timeout=1,
    ) -> None:

        # Use RendererManager to handle renderer setup and WebSocket connection
        renderer_manager = RendererManager(
            renderer=renderer,
            host=host,
            port=port,
            server_exe=server_exe,
            server_args=server_args,
            run_ws_in_thread=run_ws_in_thread,
            ping_timeout=ping_timeout,
        )

        if params_override is None:
            fea_params = FEARenderParams(solid_beams=solid_beams)
            params_override = RenderParams(unique_id=unique_id, purpose=purpose, fea_params=fea_params)

        # Set up the renderer and WebSocket server
        renderer_instance = renderer_manager.render(self, params_override)
        return renderer_instance

    def to_mesh(self) -> Mesh:
        from ada.fem.results.common import Mesh

        fem_nodes = self.nodes.to_fem_nodes()
        elem_blocks = self.elements.to_elem_blocks()
        return Mesh(elem_blocks, fem_nodes)

    @property
    def instance_name(self):
        return self.name if self.name is not None else f"{self.parent.name}-1"

    @property
    def nsets(self):
        return self.sets.nodes

    @property
    def elsets(self):
        return self.sets.elements

    @property
    def options(self) -> FemOptions:
        return self._options

    def __add__(self, other: FEM):
        # Nodes
        nodid_max = self.nodes.max_nid if len(self.nodes) > 0 else 0
        if nodid_max > other.nodes.min_nid:
            other.nodes.renumber(int(nodid_max + 10))

        self.nodes.parent = self
        self.nodes += other.nodes

        # Elements
        elid_max = self.elements.max_el_id if len(self.elements) > 0 else 0

        if elid_max > other.elements.min_el_id:
            other.elements.renumber(int(elid_max + 10))

        self.elements += other.elements
        self.sections += other.sections

        # Copy any Beam sections to the current FEM parent Part object
        if self.parent is not None:
            for sec in self.sections:
                sec.parent = self
                if sec.section is None:
                    continue
                if sec.section.parent != self.parent:
                    self.parent.add_object(sec.section)

        self.sets += other.sets

        for bc in other.bcs:
            bc.parent = self
            self.bcs.append(bc)

        for con in other.constraints.values():
            con.parent = self
            self.constraints[con.name] = con

        for name, csys in other.lcsys.items():
            csys.parent = self
            self.lcsys[name] = csys

        for name, con_sec in other.connector_sections.items():
            con_sec.parent = self
            self.connector_sections[name] = con_sec

        for name, mass in other.masses.items():
            mass.parent = self
            self.masses[name] = mass

        for name, surface in other.surfaces.items():
            surface.parent = self
            self.surfaces[name] = surface

        if self.parent is None or other.parent is None:
            return self

        self.parent.materials += other.parent.materials

        return self

    def __repr__(self):
        return f"FEM({self.name}, Elements: {len(self.elements)}, Nodes: {len(self.nodes)})"
