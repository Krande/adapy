from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, OrderedDict

from ada.core.guid import create_guid
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.scene_handling.scene_from_fea_results import scene_from_fem_results
from ada.visit.scene_handling.scene_from_fem import scene_from_fem
from ada.visit.scene_handling.scene_from_object import scene_from_object
from ada.visit.scene_handling.scene_from_part import scene_from_part_or_assembly
from ada.visit.scene_handling.scene_utils import from_z_to_y_is_up

if TYPE_CHECKING:
    import trimesh

    from ada import FEM, Assembly, Part
    from ada.api.animations import Animation
    from ada.base.physical_objects import BackendGeom
    from ada.comms.fb.fb_meshes_gen import MeshDC
    from ada.extension.design_and_analysis_extension_schema import (
        AdaDesignAndAnalysisExtension,
    )
    from ada.fem.results import FEAResult
    from ada.visit.render_params import RenderParams


@dataclass
class SceneConverter:
    """
    Handles conversion of various object types to trimesh scenes and manages
    GLTF processing, extensions, and postprocessing in a unified pipeline.
    """

    source: BackendGeom | Part | Assembly | FEAResult | FEM | trimesh.Scene | MeshDC
    params: RenderParams | None = None

    # GLTF processing components
    animations: list[Animation] = field(default_factory=list)
    extensions: dict = field(default_factory=dict)

    # Cached results

    _scene: trimesh.Scene | None = field(default=None, init=False)
    _processed_scene: trimesh.Scene | None = field(default=None, init=False)

    # Ada extension
    ada_ext: AdaDesignAndAnalysisExtension = field(init=False)
    graph: GraphStore = field(init=False)

    def __post_init__(self):
        from ada.extension.design_and_analysis_extension_schema import (
            AdaDesignAndAnalysisExtension,
        )

        if self.params is None:
            from ada.visit.render_params import RenderParams

            self.params = RenderParams()

        self.ada_ext = AdaDesignAndAnalysisExtension()

    def build_scene(self) -> trimesh.Scene:
        """Build the trimesh scene from the source object"""
        import trimesh

        from ada import FEM, Assembly, Part
        from ada.base.physical_objects import BackendGeom
        from ada.fem.results import FEAResult

        if self._scene is not None:
            return self._scene

        if self.source is None:
            raise ValueError("No source object set")

        is_part = type(self.source) is Part or type(self.source) is Assembly

        root_id = 0
        if is_part:
            root = GraphNode(self.source.name, root_id, hash=self.source.guid)
        else:
            root = GraphNode("root", root_id, hash=create_guid())

        self.graph = GraphStore(root, {root_id: root})

        has_meta = False
        if is_part:
            self._scene = scene_from_part_or_assembly(self.source, self)
            for subp in self.source.get_all_subparts(include_self=True):
                if not subp.fem.is_empty():
                    scene_from_fem(subp.fem, self)
        elif isinstance(self.source, BackendGeom):
            self._scene = scene_from_object(self.source, self)
        elif isinstance(self.source, FEM):
            self._scene = scene_from_fem(self.source, self)
        elif isinstance(self.source, FEAResult):
            self._scene = scene_from_fem_results(self.source, self)
        elif isinstance(self.source, trimesh.Scene):
            self._scene = self.source.copy()
            if "id_hierarchy" in self.source.metadata.keys():
                has_meta = True
        else:
            raise ValueError(f"Unsupported object type: {type(self.source)}")

        self.params.set_gltf_buffer_postprocessor(self.buffer_postprocessor)
        self.params.set_gltf_tree_postprocessor(self.tree_postprocessor)

        if not has_meta:
            self._scene.metadata.update(self.graph.to_json_hierarchy())

        if self.params.embed_ada_extension:
            self.add_extension("ADA_EXT_data", self.ada_ext.model_dump(mode="json"))

        return self._scene

    def build_processed_scene(self) -> trimesh.Scene:
        """Build and apply post-processing to the scene"""
        if self._processed_scene is not None:
            return self._processed_scene

        scene = self.build_scene()

        # Apply scene post-processor if available
        if self.params and self.params.scene_post_processor:
            scene = self.params.scene_post_processor(scene)

        self._processed_scene = scene
        return self._processed_scene

    def build_glb(self) -> bytes:
        """Build scene as GLB"""
        scene = self.build_processed_scene()
        if self.params.force_y_is_up:
            from_z_to_y_is_up(scene)

        data = scene.export(
            file_type="glb",
            buffer_postprocessor=self.buffer_postprocessor,
            tree_postprocessor=self.tree_postprocessor,
        )
        return data

    def build_encoded_glb(self) -> str:
        """Build and encode scene as base64 GLB"""
        data = self.build_glb()

        import base64

        return base64.b64encode(data).decode("utf-8")

    def add_animation(self, animation: Animation):
        self.animations.append(animation)

    def add_extension(self, name: str, extension: dict):
        self.extensions[name] = extension

    def _update_buffer_view(self, tree, accessor_idx, target_num):
        buffer_view_idx = tree["accessors"][accessor_idx]["bufferView"]
        buffer_view = tree["bufferViews"][buffer_view_idx]
        if buffer_view.get("target") is None:
            buffer_view["target"] = target_num

    def _update_animations(self, tree: OrderedDict):
        animations = tree.get("animations", [])
        for anim in animations:
            node_idx = anim["channels"][0]["target"]["node"]
            mesh_idx = tree["nodes"][node_idx]["mesh"]
            mesh = tree["meshes"][mesh_idx]
            for primitive in mesh["primitives"]:
                # Set ARRAY_BUFFER target for common attributes if present
                for attr in ("POSITION", "NORMAL", "TEXCOORD_0", "COLOR_0", "JOINTS_0", "WEIGHTS_0"):
                    if attr in primitive.get("attributes", {}):
                        self._update_buffer_view(tree, primitive["attributes"][attr], 34962)
                # ELEMENT_ARRAY_BUFFER for indices if present
                if "indices" in primitive:
                    self._update_buffer_view(tree, primitive["indices"], 34963)
                # Morph target POSITIONs
                for target in primitive.get("targets", []):
                    if "POSITION" in target:
                        self._update_buffer_view(tree, target["POSITION"], 34962)

    def _update_extensions(self, tree: OrderedDict):
        if tree.get("extensionsUsed") is None:
            tree["extensionsUsed"] = []
        if tree.get("extensions") is None:
            tree["extensions"] = {}

        for extension_name, extension in self.extensions.items():
            if extension_name not in tree["extensionsUsed"]:
                tree["extensionsUsed"].append(extension_name)
            if extension_name not in tree["extensions"].keys():
                tree["extensions"][extension_name] = extension

    def buffer_postprocessor(self, buffer_items, tree):
        for idx, animation in enumerate(self.animations):
            animation.process(buffer_items, tree, morph_target_index=idx, num_morph_targets=len(self.animations))

    def tree_postprocessor(self, tree: OrderedDict):
        for material in tree["materials"]:
            material["doubleSided"] = True

        self._update_animations(tree)
        if self.params.embed_ada_extension:
            self._update_extensions(tree)

        if self.params.gltf_asset_extras_dict is not None:
            extras = tree.get("asset", {}).get("extras", {})
            extras.update(self.params.gltf_asset_extras_dict)
            tree["asset"]["extras"] = extras

    @property
    def scene(self) -> trimesh.Scene:
        """Cached scene object."""
        return self._scene
