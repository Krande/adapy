from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, Callable, Union

from ada.api.spatial.part import Part
from ada.api.user import User
from ada.base.ifc_types import SpatialTypes
from ada.base.types import GeomRepr
from ada.base.units import Units
from ada.cache.store import CacheStore
from ada.config import Config, logger
from ada.fem import (
    Connector,
    Csys,
    FemSet,
    StepEigen,
    StepExplicit,
    StepImplicitStatic,
    StepSteadyState,
)

_step_types = Union[StepSteadyState, StepEigen, StepImplicitStatic, StepExplicit]

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

    import ifcopenshell
    import ifcopenshell.validate

    from ada.cadit.ifc.store import IfcStore
    from ada.fem.formats.general import FEATypes, FemConverters
    from ada.fem.results.common import FEAResult


class FormatNotSupportedException(Exception):
    pass


class Assembly(Part):
    """The Assembly object. A top level container of parts, beams, plates, shapes and FEM."""

    def __init__(
        self,
        name="Ada",
        project="AdaProject",
        user: User = User(),
        schema="IFC4X3_add2",
        metadata=None,
        units: Units | str = Units.M,
        enable_cache: bool = False,
        clear_cache: bool = False,
        cache_dir: str | pathlib.Path = None,
        ifc_class: SpatialTypes = SpatialTypes.IfcSite,
    ):
        metadata = dict() if metadata is None else metadata
        metadata["project"] = project
        metadata["schema"] = schema
        super(Assembly, self).__init__(name=name, metadata=metadata, units=units)
        self.fem.parent = self
        user.parent = self
        self._user = user

        self._ifc_class = ifc_class
        self._ifc_store = None
        self._ifc_file = None
        self._ifc_sections = None
        self._ifc_materials = None
        self._source_ifc_files = dict()

        self._cache_store = None
        if enable_cache:
            self._cache_store = CacheStore(name, cache_dir=cache_dir)
            self.cache_store.sync(self, clear_cache=clear_cache)

    def read_ifc(
        self, ifc_file: str | os.PathLike | ifcopenshell.file, data_only=False, elements2part=None, create_cache=False
    ):
        """Import from IFC file."""
        import ifcopenshell

        if self.cache_store is not None and isinstance(ifc_file, ifcopenshell.file) is False:
            if self.cache_store.from_cache(self, ifc_file) is True:
                return None

        self.ifc_store.load_ifc_content_from_file(ifc_file, data_only=data_only, elements2part=elements2part)

        if self.cache_store is not None:
            self.cache_store.to_cache(self, ifc_file, create_cache)

    def read_fem(
        self,
        fem_file: str | os.PathLike,
        fem_format: FEATypes | str = None,
        name: str = None,
        fem_converter: FemConverters | str = "default",
        cache_model_now=False,
    ):
        """Import a Finite Element model. Currently supported FEM formats: Abaqus, Sesam and Calculix"""
        from ada.fem.formats.general import get_fem_converters

        fem_file = pathlib.Path(fem_file)
        if fem_file.exists() is False:
            raise FileNotFoundError(fem_file)

        if self.cache_store is not None:
            if self.cache_store.from_cache(self, fem_file) is True:
                return None

        fem_importer, _ = get_fem_converters(fem_file, fem_format, fem_converter)
        if fem_importer is None:
            suffix = fem_file.suffix
            raise FormatNotSupportedException(f'File "{fem_file.name}" [{suffix}] is not a supported FEM format.')

        temp_assembly: Assembly = fem_importer(fem_file, name)
        self.__add__(temp_assembly)

        if self.cache_store is not None:
            self.cache_store.to_cache(self, fem_file, cache_model_now)

    def to_fem(
        self,
        name: str,
        fem_format: FEATypes | str,
        scratch_dir=None,
        metadata=None,
        execute=False,
        run_ext=False,
        mesh_only=False,
        cpus=1,
        gpus=None,
        overwrite=False,
        fem_converter="default",
        exit_on_complete=True,
        run_in_shell=False,
        make_zip_file=False,
        return_fea_results=True,
        model_data_only=False,
    ) -> FEAResult | None:
        """
        Create a FEM input file deck for executing fem analysis in a specified FEM format.
        Currently there is limited write support for the following FEM formats:

        Open Source

        * Calculix
        * Code_Aster

        not open source

        * Abaqus
        * Usfos
        * Sesam


        Write support is added on a need-only-basis. Any contributions are welcomed!

        :param name: Name of FEM analysis input deck
        :param fem_format: Desired fem format
        :param scratch_dir: Output directory for analysis input deck
        :param metadata: Parse additional commands to FEM solver not supported by the generalized classes
        :param execute: Execute analysis on complete
        :param run_ext: Run analysis externally or wait for complete
        :param cpus: Number of cpus for running the analysis
        :param gpus: Number of gpus for running the analysis (wherever relevant)
        :param overwrite: Overwrite existing input file deck
        :param fem_converter: Set desired fem converter. Use either 'default' or 'meshio'.
        :param exit_on_complete:
        :param run_in_shell:
        :param make_zip_file:
        :param return_fea_results: Automatically import the result mesh into

            Note! Meshio implementation currently only supports reading & writing elements and nodes.

        Abaqus Metadata:

            'ecc_to_mpc': Runs the method :func:`~ada.fem.FEM.convert_ecc_to_mpc` . Default is True
            'hinges_to_coupling': Runs the method :func:`~ada.fem.FEM.convert_hinges_2_couplings` . Default is True

            Important Note! The ecc_to_mpc and hinges_to_coupling will make permanent modifications to the model.
            If this proves to create issues regarding performance this should be evaluated further.

        """
        from ada.fem.formats.execute import execute_fem
        from ada.fem.formats.general import FEATypes, write_to_fem
        from ada.fem.formats.postprocess import postprocess
        from ada.fem.formats.utils import default_fem_res_path

        if isinstance(fem_format, str):
            fem_format = FEATypes.from_str(fem_format)

        scratch_dir = Config().fea_scratch_dir if scratch_dir is None else pathlib.Path(scratch_dir)

        write_to_fem(
            self, name, fem_format, overwrite, fem_converter, scratch_dir, metadata, make_zip_file, model_data_only
        )

        # Execute
        if execute:
            execute_fem(
                name, fem_format, scratch_dir, cpus, gpus, run_ext, metadata, execute, exit_on_complete, run_in_shell
            )

        # Gather results
        fem_res_files = default_fem_res_path(name, scratch_dir=scratch_dir)
        res_path = fem_res_files.get(fem_format, None)

        if res_path.exists() is False or return_fea_results is False:
            return None

        return postprocess(res_path, fem_format=fem_format)

    def to_ifc(
        self,
        destination=None,
        include_fem=False,
        file_obj_only=False,
        validate=False,
        progress_callback: Callable[[int, int], None] = None,
        geom_repr_override: dict[str, GeomRepr] = None,
    ) -> ifcopenshell.file:
        import ifcopenshell.validate

        if destination is None or file_obj_only is True:
            destination = "object"
        else:
            destination = pathlib.Path(destination).resolve().absolute()

        print(f'Beginning writing to IFC file "{destination}" using IfcOpenShell')

        self.ifc_store.sync(
            include_fem=include_fem, progress_callback=progress_callback, geom_repr_override=geom_repr_override
        )

        if file_obj_only is False:
            os.makedirs(destination.parent, exist_ok=True)
            self.ifc_store.save_to_file(destination)

        if validate:
            ifcopenshell.validate.validate(self.ifc_store.f if file_obj_only else destination, logger)

        print("IFC file creation complete")
        return self.ifc_store.f

    def to_genie_xml(
        self, destination_xml, writer_postprocessor: Callable[[ET.Element, Part], None] = None, embed_sat=False
    ):
        from ada.cadit.gxml.write.write_xml import write_xml

        write_xml(self, destination_xml, writer_postprocessor=writer_postprocessor, embed_sat=embed_sat)

    def push(self, comment, bimserver_url, username, password, project, merge=False, sync=False):
        """Push current assembly to BimServer with a comment tag that defines the revision name"""
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.push(project, comment, merge, sync)

    def pull(self, bimserver_url, username, password, project, checkout=False):
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.pull(project, checkout)

    def get_ifc_source_by_name(self, ifc_file):
        from ada.cadit.ifc.read.reader_utils import open_ifc

        if ifc_file not in self._source_ifc_files.keys():
            ifc_f = open_ifc(ifc_file)
            self._source_ifc_files[ifc_file] = ifc_f
        else:
            ifc_f = self._source_ifc_files[ifc_file]

        return ifc_f

    @property
    def ifc_store(self) -> IfcStore:
        if self._ifc_store is None:
            from ada.cadit.ifc.store import IfcStore
            from ada.cadit.ifc.utils import assembly_to_ifc_file

            self._ifc_file = assembly_to_ifc_file(self)
            self._ifc_store = IfcStore(assembly=self)

        return self._ifc_store

    @ifc_store.setter
    def ifc_store(self, value):
        self._ifc_store = value

    @property
    def user(self) -> User:
        return self._user

    @property
    def cache_store(self) -> CacheStore:
        return self._cache_store

    def __add__(self, other: Assembly | Part):
        if other.units != self.units:
            other.units = self.units

        for interface_n in other.fem.interface_nodes:
            n = interface_n.node
            for p in self.get_all_parts_in_assembly(True):
                res = p.fem.nodes.get_by_volume(n.p)
                if res is not None and len(res) > 0:
                    replace_node = res[0]
                    for ref in n.refs:
                        if isinstance(ref, Connector):
                            if n == ref.n1:
                                ref.n1 = replace_node
                            elif n == ref.n2:
                                ref.n2 = replace_node
                            else:
                                logger.warning(f'No matching node found for either n1 or n2 of "{ref}"')
                        elif isinstance(ref, Csys):
                            index = ref.nodes.index(n)
                            ref.nodes.pop(index)
                            ref.nodes.insert(index, replace_node)
                        elif isinstance(ref, FemSet):
                            index = ref.members.index(n)
                            ref.members.pop(index)
                            ref.members.insert(index, replace_node)
                        else:
                            raise NotImplementedError(f'Unsupported type "{type(ref)}"')
                    break

        self.fem += other.fem

        for p in other.parts.values():
            p.parent = self
            self.add_part(p)

        for mat in other.materials:
            if mat not in self.materials:
                self.materials.add(mat)

        self.sections += other.sections
        self.shapes += other.shapes
        self.beams += other.beams
        self.plates += other.plates
        self.pipes += other.pipes
        self.walls += other.walls
        return self

    def __repr__(self):
        nbms = len([bm for p in self.get_all_subparts() for bm in p.beams]) + len(self.beams)
        npls = len([pl for p in self.get_all_subparts() for pl in p.plates]) + len(self.plates)
        nshps = len([shp for p in self.get_all_subparts() for shp in p.shapes]) + len(self.shapes)
        npipes = len(self.pipes) + len([pl for p in self.get_all_subparts() for pl in p.pipes])
        nels = len(self.fem.elements) + len([el for p in self.get_all_subparts() for el in p.fem.elements])
        nns = len(self.fem.nodes) + len([no for p in self.get_all_subparts() for no in p.fem.nodes])
        return (
            f'Assembly("{self.name}": Beams: {nbms}, Plates: {npls}, Pipes: {npipes}, '
            f"Shapes: {nshps}, Elements: {nels}, Nodes: {nns})"
        )
