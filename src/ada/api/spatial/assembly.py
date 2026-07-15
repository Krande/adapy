from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, Callable, Literal, Union

from ada.api.spatial.part import Part
from ada.api.user import User
from ada.base.ifc_types import SpatialTypes
from ada.base.types import GeomRepr
from ada.base.units import Units
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
        ifc_class: SpatialTypes = SpatialTypes.IfcSite,
        cad_config=None,
    ):
        metadata = dict() if metadata is None else metadata
        metadata["project"] = project
        metadata["schema"] = schema
        super(Assembly, self).__init__(name=name, metadata=metadata, units=units)
        self.fem.parent = self
        user.parent = self
        self._user = user

        self._cad_config = cad_config  # ada.cad.CadConfig | None (lazy default on first access)
        self._ifc_class = ifc_class
        self._ifc_store = None
        self._ifc_file = None
        self._ifc_sections = None
        self._ifc_materials = None
        self._source_ifc_files = dict()

    @property
    def cad_config(self):
        """CAD backend + tessellation-path config (:class:`ada.cad.CadConfig`).

        Defaults lazily to the best path available in the environment — libtess2 when adacpp is
        installed (OCC-free, step2glb-parity), else OCC. Set it to pick a path explicitly; pass
        it on to factory functions, e.g. ``stream_step_to_glb(..., cad_config=asm.cad_config)``."""
        if self._cad_config is None:
            from ada.cad import CadConfig

            self._cad_config = CadConfig.default()
        return self._cad_config

    @cad_config.setter
    def cad_config(self, value):
        self._cad_config = value

    def __getstate__(self):
        # ifcopenshell.file and ifcopenshell.geom.settings are C-bound and
        # don't pickle. Both _ifc_store and _source_ifc_files are caches over
        # lazily generated state; clearing them lets the assembly cross a
        # process boundary cleanly and the caches rebuild on next access.
        state = self.__dict__.copy()
        state["_ifc_store"] = None
        state["_ifc_file"] = None
        state["_source_ifc_files"] = {}
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

    def read_ifc(
        self,
        ifc_file: str | os.PathLike | ifcopenshell.file,
        data_only=False,
        elements2part=None,
        reader: Literal["ifcopenshell", "native"] | None = None,
    ):
        """Import from IFC file.

        ``reader="native"`` uses adacpp's pure-C++ IFC reader (IfcNgeomStream) to build a
        geometry-shapes Part/ShapeProxy tree (no ifcopenshell/OCC) — colour + spatial hierarchy from
        the C++ resolver; does NOT reconstruct typed Beam/Plate objects. Default (``ifcopenshell``)
        is the full typed reader.
        """
        if reader == "native":
            from ada.cadit.ifc.read.native_reader import (
                native_adacpp_ifc_available,
                native_read_ifc_into,
            )

            if not native_adacpp_ifc_available():
                raise RuntimeError("reader='native' requires adacpp with IfcNgeomStream")
            native_read_ifc_into(self, ifc_file)
            if isinstance(ifc_file, (str, os.PathLike)):
                self.ifc_store.ifc_file_path = pathlib.Path(ifc_file)
            return
        self.ifc_store.load_ifc_content_from_file(ifc_file, data_only=data_only, elements2part=elements2part)

    def read_fem(
        self,
        fem_file: str | os.PathLike,
        fem_format: FEATypes | str = None,
        name: str = None,
        fem_converter: FemConverters | str = "default",
    ):
        """Import a Finite Element model. Currently supported FEM formats: Abaqus, Sesam and Calculix"""
        from ada.fem.formats.general import get_fem_converters

        fem_file = pathlib.Path(fem_file)
        if fem_file.exists() is False:
            raise FileNotFoundError(fem_file)

        fem_importer, _ = get_fem_converters(fem_file, fem_format, fem_converter)
        if fem_importer is None:
            suffix = fem_file.suffix
            raise FormatNotSupportedException(f'File "{fem_file.name}" [{suffix}] is not a supported FEM format.')

        temp_assembly: Assembly = fem_importer(fem_file, name)
        self.__add__(temp_assembly)

    def to_fem(
        self,
        name: str,
        fem_format: FEATypes | str,
        scratch_dir=None,
        metadata=None,
        execute=False,
        run_ext=False,
        cpus=1,
        gpus=None,
        overwrite=False,
        fem_converter="default",
        exit_on_complete=True,
        run_in_shell=False,
        make_zip_file=False,
        return_fea_results=True,
        model_data_only=False,
        write_input_files_only=False,
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
        :param model_data_only: Only write the model data (nodes, elements, etc.) to the FEM file
        :param write_input_files_only: Only write the input files, do not execute the analysis

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

        # Gather results
        fem_res_files = default_fem_res_path(name, scratch_dir=scratch_dir)
        res_path = fem_res_files.get(fem_format, None)

        if res_path.exists() and overwrite is False and return_fea_results is True and write_input_files_only is False:
            logger.info(f"FEM result file already exists: {res_path}")
            return postprocess(res_path, fem_format=fem_format)

        write_to_fem(
            self, name, fem_format, overwrite, fem_converter, scratch_dir, metadata, make_zip_file, model_data_only
        )

        if write_input_files_only:
            return None

        # Execute
        if execute:
            execute_fem(
                name, fem_format, scratch_dir, cpus, gpus, run_ext, metadata, execute, exit_on_complete, run_in_shell
            )

        # Honor return_fea_results=False even when the solver DID produce a
        # result file: callers that pass it (e.g. a build that only publishes the
        # .rmed) don't want the in-memory read, and postprocessing here would
        # force a result-parse they never asked for (and can trip an unsupported
        # field-profile branch on an otherwise-successful solve).
        if return_fea_results is False:
            return None

        if res_path.exists() is False:
            if execute:
                raise FileNotFoundError(f"FEM result file does not exist: {res_path}")
            logger.info(f"FEM result file does not exist: {res_path}")
            return None

        return postprocess(res_path, fem_format=fem_format)

    def to_pickle(self, pickle_file: str | pathlib.Path) -> pathlib.Path:
        """Serialize this Assembly to a pickle file (round-trips via :func:`ada.from_pickle`).

        adapy objects are kept picklable on purpose — backend CAD bodies live in the transient
        ``_occ_cache`` slot, not on the object — so the parametric model round-trips cleanly. Lets
        a source parsed once be reused for many exports without re-reading/re-parsing it.
        """
        import pickle
        import tempfile

        pickle_file = pathlib.Path(pickle_file)
        pickle_file.parent.mkdir(parents=True, exist_ok=True)
        # atomic write so a concurrent reader never sees a half-written pickle
        fd, tmp = tempfile.mkstemp(dir=str(pickle_file.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, pickle_file)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return pickle_file

    def to_ifc(
        self,
        destination=None,
        include_fem=False,
        file_obj_only=False,
        validate=False,
        progress_callback: Callable[[int, int], None] = None,
        geom_repr_override: dict[str, GeomRepr] = None,
        streaming=False,
        merge_strategy=None,
        writer: Literal["ifcopenshell", "native"] | None = None,
    ) -> ifcopenshell.file:
        import ifcopenshell.validate

        if destination is None or file_obj_only is True:
            destination = "object"
        else:
            destination = pathlib.Path(destination).resolve().absolute()

        # Native pure-C++ writer (adacpp blobs_to_ifc): emit analytic IFC solids from each shape's
        # NGEOM blob — no ifcopenshell/OCC. Needs an on-disk destination + lazy ShapeProxy shapes
        # (pairs with from_ifc(reader="native")). Raises if no shape carries a blob.
        if writer == "native":
            if destination == "object":
                raise ValueError("to_ifc(writer='native') needs an on-disk destination")
            from ada.cadit.ifc.write.native_ifc_writer import (
                native_ifc_writer_available,
                native_write_ifc,
            )

            if not native_ifc_writer_available():
                raise RuntimeError("writer='native' requires adacpp with blobs_to_ifc")
            os.makedirs(destination.parent, exist_ok=True)
            native_write_ifc(self, destination)
            if validate:
                ifcopenshell.validate.validate(str(destination), logger)
            logger.info("IFC file creation complete (native)")
            return None

        logger.info(f'Beginning writing to IFC file "{destination}" using IfcOpenShell')

        # Memory-bounded path: hand-author Plate solids as SPF text instead of
        # holding the whole ifcopenshell.file in memory. It rebuilds the IFC
        # from the assembly's concept objects, so it's only correct for freshly
        # built models. Fall back to the in-memory writer when:
        #   * there is no on-disk destination / a geom_repr_override is set, or
        #   * the model was loaded from IFC — ifc_store.f then already holds the
        #     source products (objects are NOCHANGE) and the normal writer's
        #     passthrough is required; rebuilding them from scratch fails.
        if streaming and not file_obj_only and destination != "object" and geom_repr_override is None:
            if not self.ifc_store.f.by_type("IfcProduct"):
                from ada.cadit.ifc.write.stream_ifc import stream_assembly_to_ifc

                stream_assembly_to_ifc(
                    self,
                    destination,
                    include_fem=include_fem,
                    progress_callback=progress_callback,
                    merge_strategy=merge_strategy,
                )
                if validate:
                    ifcopenshell.validate.validate(destination, logger)
                logger.info("IFC file creation complete (streaming)")
                return None
            logger.info("to_ifc(streaming=True): model carries loaded IFC entities; using the in-memory writer")
        elif streaming:
            logger.warning(
                "to_ifc(streaming=True) needs an on-disk destination and no geom_repr_override; "
                "falling back to the in-memory writer."
            )

        self.ifc_store.sync(
            include_fem=include_fem, progress_callback=progress_callback, geom_repr_override=geom_repr_override
        )

        if file_obj_only is False:
            os.makedirs(destination.parent, exist_ok=True)
            self.ifc_store.save_to_file(destination)

        if validate:
            ifcopenshell.validate.validate(self.ifc_store.f if file_obj_only else destination, logger)

        logger.info("IFC file creation complete")
        return self.ifc_store.f

    def to_genie_xml(
        self,
        destination_xml,
        writer_postprocessor: Callable[[ET.Element, Part], None] = None,
        embed_sat: bool | None = None,
        streaming: bool = False,
        merge_strategy=None,
    ):
        """Write a Genie (DNV) concept XML.

        ``embed_sat`` embeds the plate geometry as a ready-built ACIS SAT body
        that each ``<flat_plate>`` references by face name. Without it the plates
        are written as bare polygons and Genie must rebuild — and imprint — the
        ACIS itself on import, which dominates load time on a large model. It
        needs a CAD backend (see ``CadBackend.imprint_planar_faces``).

        Defaults to ``None`` = on whenever it can be produced. It can't be with
        ``merge_strategy``, which sources plates from the FEM-shell face engine
        without ever materialising the ``Plate`` objects the SAT body is built
        from; asking for both explicitly is contradictory and raises rather than
        quietly dropping one.

        ``streaming`` emits the per-object ``<structure>`` entries straight to
        the file instead of building the whole DOM, cutting peak RSS on large
        FEM-derived models. It composes with ``embed_sat`` (the SAT body itself
        is inherently whole-model, so only the concept entries stream).

        ``merge_strategy`` (None | "none" | "coplanar" | ...) sources plates from
        the object-free vectorized FEM-shell face engine — streaming path only.
        """
        if merge_strategy is not None:
            if embed_sat:
                raise ValueError(
                    "to_genie_xml: embed_sat=True is incompatible with merge_strategy="
                    f"{merge_strategy!r} — the SAT body is built from Plate objects, which the "
                    "merge_strategy face source never materialises. Pass one or the other."
                )
            if embed_sat is None:
                embed_sat = False
                logger.info("to_genie_xml: merge_strategy set, so plates are written as polygons (no SAT)")
        elif embed_sat is None:
            embed_sat = True

        if merge_strategy is not None and not streaming:
            raise ValueError(
                f"to_genie_xml: merge_strategy={merge_strategy!r} is only honoured on the streaming "
                "path; pass streaming=True (it was previously ignored here)."
            )

        if streaming:
            from ada.cadit.gxml.write.stream_xml import write_xml_stream

            write_xml_stream(
                self,
                destination_xml,
                writer_postprocessor=writer_postprocessor,
                merge_strategy=merge_strategy,
                embed_sat=embed_sat,
            )
        else:
            from ada.cadit.gxml.write.write_xml import write_xml

            write_xml(self, destination_xml, writer_postprocessor=writer_postprocessor, embed_sat=embed_sat)
        logger.info(f'Genie XML file "{destination_xml}" created')

        return destination_xml

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

            f = assembly_to_ifc_file(self)

            self._ifc_file = f
            self._ifc_store = IfcStore(assembly=self)

        return self._ifc_store

    @ifc_store.setter
    def ifc_store(self, value):
        self._ifc_store = value

    @property
    def user(self) -> User:
        return self._user

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
