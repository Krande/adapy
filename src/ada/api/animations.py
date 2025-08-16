from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from trimesh.exchange.gltf import _data_append

from ada import logger


@dataclass
class Animation:
    name: str
    keyframe_times: list[float | int]
    translation_keyframes: list[float | int] = None
    rotation_keyframes: list[list[float, float, float, float]] = None
    deformation_weights_keyframes: list[float | int] = None
    deformation_shape: list[list[float]] = None
    node_idx: int | list[int] = None
    # Optional: mapping from buffer_id -> expanded edge vertex source indices
    edge_mappings: dict[int, list[int]] | None = None

    def process(self, buffer_items, tree, morph_target_index, num_morph_targets):
        node_idx_list = self.node_idx
        if not isinstance(node_idx_list, list):
            node_idx_list = [node_idx_list]

        keyframe_idx = _data_append(
            acc=tree["accessors"],
            buff=buffer_items,
            blob={"componentType": 5126, "type": "SCALAR"},
            data=np.array(self.keyframe_times, dtype="float32"),
        )

        samplers = []
        channels = []
        if self.translation_keyframes is not None:
            translation_idx = _data_append(
                acc=tree["accessors"],
                buff=buffer_items,
                blob={"componentType": 5126, "type": "VEC3"},
                data=np.array(self.translation_keyframes, dtype="float32"),
            )
            translation_sampler = {"input": keyframe_idx, "interpolation": "LINEAR", "output": translation_idx}
            sampler_idx = len(samplers)
            samplers.append(translation_sampler)
            for node_idx in node_idx_list:
                translation_channel = {"sampler": sampler_idx, "target": {"node": node_idx, "path": "translation"}}
                channels.append(translation_channel)

        if self.rotation_keyframes is not None:
            rotation_idx = _data_append(
                acc=tree["accessors"],
                buff=buffer_items,
                blob={"componentType": 5126, "type": "VEC4"},
                data=np.array(self.rotation_keyframes, dtype="float32"),
            )
            rotation_sampler = {"input": keyframe_idx, "interpolation": "LINEAR", "output": rotation_idx}
            sampler_idx = len(samplers)
            samplers.append(rotation_sampler)
            for node_idx in node_idx_list:
                rotation_channel = {"sampler": sampler_idx, "target": {"node": node_idx, "path": "rotation"}}
                channels.append(rotation_channel)

        if self.deformation_weights_keyframes is not None:
            # Per glTF 2.0, animation output for 'weights' must be SCALAR with
            # length = keyframe_count * num_morph_targets (flattened by keyframe)
            keyframe_weights = np.zeros((len(self.deformation_weights_keyframes) * num_morph_targets), dtype="float32")
            for i, weight in enumerate(self.deformation_weights_keyframes):
                keyframe_weights[i * num_morph_targets + morph_target_index] = float(weight)

            deformation_weights_keys_idx = _data_append(
                acc=tree["accessors"],
                buff=buffer_items,
                blob={"componentType": 5126, "type": "SCALAR"},
                data=keyframe_weights,
            )

            deformation_sampler = {
                "input": keyframe_idx,
                "interpolation": "LINEAR",
                "output": deformation_weights_keys_idx,
            }
            sampler_idx = len(samplers)
            samplers.append(deformation_sampler)

            for node_idx in node_idx_list:
                deformation_channel = {"sampler": sampler_idx, "target": {"node": node_idx, "path": "weights"}}
                channels.append(deformation_channel)

                # add position targets per node (mesh primitive)
                mesh_no = tree["nodes"][node_idx]["mesh"]
                mesh = tree["meshes"][mesh_no]
                primitive = mesh["primitives"][0]

                # Try to detect primitive mode if available (1 = LINES)
                mode = primitive.get("mode", 4)

                # Build deformation array for this node
                deform = None
                if mode == 1 and self.edge_mappings is not None:
                    # Map from node name 'node{buffer_id}' to buffer_id
                    node_name = tree["nodes"][node_idx].get("name", "")
                    buf_id = None
                    if node_name.startswith("node"):
                        try:
                            buf_id = int(node_name.replace("node", "").split("_")[0])
                        except Exception:
                            buf_id = None
                    if buf_id is not None and buf_id in self.edge_mappings:
                        mapping = self.edge_mappings[buf_id]
                        # Expect mapping length equal to base_count
                        try:
                            deltas = np.asarray(self.deformation_shape, dtype="float32").reshape(-1, 3)
                            deform = deltas[np.asarray(mapping, dtype=np.int64)]
                        except Exception as e:
                            logger.warning(e)
                            deform = None
                else:
                    # Faces/points: assume provided deformation_shape aligns with vertex order
                    if self.deformation_shape is not None:
                        deform = np.asarray(self.deformation_shape, dtype="float32").reshape(-1, 3)

                deformation_shape_idx = _data_append(
                    acc=tree["accessors"],
                    buff=buffer_items,
                    blob={"componentType": 5126, "type": "VEC3"},
                    data=deform,
                )

                if primitive.get("targets") is None:
                    primitive["targets"] = []
                primitive["targets"].append({"POSITION": deformation_shape_idx})

                if "extras" not in mesh:
                    mesh["extras"] = {}
                if mesh["extras"].get("targetNames") is None:
                    mesh["extras"]["targetNames"] = []
                mesh["extras"]["targetNames"].append(self.name)

                if mesh.get("weights") is None:
                    mesh["weights"] = []
                # Initialize the default morph target weight to 0.0
                mesh["weights"].append(0.0)

        # Update tree with data added to buffer
        if tree.get("animations") is None:
            tree["animations"] = []

        tree["animations"].append(
            {
                "name": self.name,
                "samplers": samplers,
                "channels": channels,
            }
        )
