from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, OrderedDict

from ada.config import logger
from ada.core.guid import create_guid
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.scene_handling.scene_from_fea_results import scene_from_fem_results
from ada.visit.scene_handling.scene_from_fem import scene_from_fem
from ada.visit.scene_handling.scene_from_object import scene_from_object
from ada.visit.scene_handling.scene_from_part import scene_from_part_or_assembly
from ada.visit.scene_handling.scene_from_step_stream import (
    StepStreamSource,
    scene_from_step_stream,
)
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

    # Staged uint32 bufferViews for SimGroup.members_buffer_view. Each
    # tuple holds (placeholder_index_set_on_simgroup, raw bytes). The
    # placeholder is rewritten to a real bufferView index in
    # ``buffer_postprocessor`` once trimesh has assigned real indices.
    _lineage_buffer_queue: list = field(default_factory=list, init=False, repr=False)
    # Sentinel base for placeholder values: members_buffer_view fields
    # carry ``_LINEAGE_PLACEHOLDER_BASE + queue_index`` before resolution.
    # Picked well above any plausible real bufferView count so the
    # postprocessor can detect them unambiguously.
    _LINEAGE_PLACEHOLDER_BASE = 2_000_000_000

    # Memoised quantity take-off (``asset.extras["model_stats"]``). The
    # ``_done`` flag rather than a None check, so a source that legitimately has
    # no take-off is not recomputed on every export.
    _model_stats: dict | None = field(default=None, init=False, repr=False)
    _model_stats_done: bool = field(default=False, init=False, repr=False)

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

        is_part = isinstance(self.source, (Part, Assembly))

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
        elif isinstance(self.source, StepStreamSource):
            self._scene = scene_from_step_stream(self.source, self)
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

        # Stamp the source Assembly's guid on the extension so derived
        # files (CAD GLB + FEA GLBs) carry a stable lineage anchor. The
        # frontend matches them by this value instead of by name.
        if is_part:
            self.ada_ext.assembly_guid = self.source.get_assembly().guid

        # Connection-component lineage (spec_name, spec_inputs,
        # member roles) is intentionally NOT written into the GLB
        # extension here — the bake's manifest.json (sibling to the
        # GLB) is the source of truth for those fields, and the
        # extension would just duplicate them per-GLB with no link
        # to mesh content. The viewer fetches manifest.json once and
        # joins by spec name. For real-model connection grouping
        # (which mesh nodes belong to which Connection part), the
        # design extension's per-part DesignDataExtension already
        # carries that — Connection's Part subtree gets the same
        # treatment as any other Part.

        # The extension is dumped from ``self.ada_ext`` inside
        # ``tree_postprocessor`` (after ``buffer_postprocessor`` has
        # resolved any SimGroup.members_buffer_view placeholders). That
        # keeps the dump and the bufferView indices in sync for the
        # large-FEA hybrid encoding.

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
            mesh_idx = tree["nodes"][node_idx].get("mesh")
            if mesh_idx is None:
                # glTF allows an animation channel to target any node, and a
                # node is not required to have a mesh. The buffer-view fixups
                # below are all mesh/morph-target work, so a channel driving a
                # meshless node (translation/rotation/scale only) has nothing
                # to do here -- and indexing "mesh" would raise KeyError.
                continue
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

    def queue_lineage_buffer(self, raw_bytes: bytes) -> int:
        """Stage a binary payload for emission as a glTF bufferView.

        Returns a placeholder integer to store on the SimGroup's
        ``members_buffer_view`` field; the placeholder is rewritten to
        the real bufferView index in ``buffer_postprocessor`` once
        trimesh has assigned indices to all buffer items."""
        self._lineage_buffer_queue.append(raw_bytes)
        return self._LINEAGE_PLACEHOLDER_BASE + len(self._lineage_buffer_queue) - 1

    def buffer_postprocessor(self, buffer_items, tree):
        for idx, animation in enumerate(self.animations):
            animation.process(buffer_items, tree, morph_target_index=idx, num_morph_targets=len(self.animations))
        self._consume_lineage_buffers(buffer_items)

    def _consume_lineage_buffers(self, buffer_items) -> None:
        """Append queued lineage payloads to the GLB binary and rewrite
        the corresponding SimGroup placeholders to real bufferView
        indices.

        Trimesh creates one bufferView per ``buffer_items`` entry in
        insertion order (see ``_build_views`` in trimesh.exchange.gltf),
        so the new bufferView's index equals the new key's position in
        the dict. Adding a unique string key avoids collisions with
        mesh-derived items."""
        if not self._lineage_buffer_queue:
            return
        base_idx = len(buffer_items)
        placeholder_to_real: dict[int, int] = {}
        for i, payload in enumerate(self._lineage_buffer_queue):
            key = f"_lineage_members_{i}"
            # Defensive: in the unlikely case of a name collision, pick
            # the next free suffix. Trimesh keys are mesh-derived so
            # collisions shouldn't happen, but the dict insertion order
            # is load-bearing here.
            suffix = 0
            while key in buffer_items:
                suffix += 1
                key = f"_lineage_members_{i}_{suffix}"
            buffer_items[key] = payload
            placeholder = self._LINEAGE_PLACEHOLDER_BASE + i
            placeholder_to_real[placeholder] = base_idx + i
        self._rewrite_lineage_placeholders(placeholder_to_real)
        # Drop the queue so a subsequent re-export of the same converter
        # doesn't double-write.
        self._lineage_buffer_queue.clear()

    def _rewrite_lineage_placeholders(self, mapping: dict[int, int]) -> None:
        """Walk every SimGroup in the staged extension and replace
        ``members_buffer_view`` placeholder values with the real bufferView
        indices."""
        for sim in self.ada_ext.simulation_objects or []:
            for grp in sim.groups or []:
                pv = grp.members_buffer_view
                if pv is None:
                    continue
                if pv in mapping:
                    grp.members_buffer_view = mapping[pv]

    def tree_postprocessor(self, tree: OrderedDict):
        # A material-less scene (e.g. line-only, or a re-tessellated import that
        # carries no materials) has no "materials" key at all — don't KeyError.
        for material in tree.get("materials") or []:
            material["doubleSided"] = True

        self._update_animations(tree)
        if self.params.embed_ada_extension:
            # Dump the extension now (post-buffer-postprocessor) so the
            # JSON contains the resolved bufferView indices for any
            # SimGroup that took the large-element binary path.
            self.add_extension("ADA_EXT_data", self.ada_ext.model_dump(mode="json"))
            self._update_extensions(tree)

        extras_updates: dict = {}
        if self.params.embed_model_stats:
            model_stats = self.build_model_stats()
            if model_stats is not None:
                extras_updates["model_stats"] = model_stats
        explicit_extras = self.params.gltf_asset_extras_dict
        if explicit_extras is not None:
            # An explicit extras dict wins over the computed take-off, so a
            # caller can override or blank out ``model_stats`` if it needs to.
            extras_updates.update(explicit_extras)
        if explicit_extras is not None or extras_updates:
            asset = tree.setdefault("asset", {})
            extras = asset.get("extras") or {}
            extras.update(extras_updates)
            asset["extras"] = extras

    def build_model_stats(self) -> dict | None:
        """Discipline-organised quantity take-off for a Part/Assembly source.

        The GLB carries only triangles, so per-discipline mass / centre-of-
        gravity / beam-and-plate quantities can only come from the structured
        model. The hosted (REST) viewer gets these from a ``.stats.json``
        sidecar written by the compile worker; on the local ``.show()`` path
        there is no server, so we embed the same document in the GLB itself
        (``asset.extras["model_stats"]``) and the viewer reads it from there.

        Returns ``None`` for non-Part sources (FEA results, raw scenes) and for
        a take-off that raised — statistics are a nicety, never a reason for a
        render to fail."""
        if self._model_stats_done:
            return self._model_stats

        self._model_stats_done = True

        from ada import Assembly, Part

        if not isinstance(self.source, (Part, Assembly)):
            return None

        try:
            from ada.topo_model.takeoff import model_takeoff

            self._model_stats = model_takeoff(self.source, source_name=self.source.name)
        except Exception as e:
            logger.warning(f"Unable to compute model take-off for {self.source!r}: {e}")
            self._model_stats = None

        return self._model_stats

    @property
    def scene(self) -> trimesh.Scene:
        """Cached scene object."""
        return self._scene
