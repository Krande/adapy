from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from trimesh.exchange.gltf import _data_append


@dataclass
class Animation:
    name: str
    keyframe_times: list[float | int]
    translation_keyframes: list[float | int] = None
    rotation_keyframes: list[list[float, float, float, float]] = None
    deformation_weights_keyframes: list[float | int] = None
    deformation_shape: list[list[float]] = None
    node_idx: int | list[int] = None

    def __call__(self, buffer_items, tree, morph_target_index, num_morph_targets):
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
            deformation_shape_idx = _data_append(
                acc=tree["accessors"],
                buff=buffer_items,
                blob={"componentType": 5126, "type": "VEC3"},
                data=np.array(self.deformation_shape, dtype="float32"),
            )

            keyframe_weights = np.zeros((len(self.deformation_weights_keyframes) * num_morph_targets), dtype="float32")
            for i, weight in enumerate(self.deformation_weights_keyframes):
                keyframe_weights[i * num_morph_targets + morph_target_index] = weight

            # print(keyframe_weights)
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

                # add position targets
                mesh_no = tree["nodes"][node_idx]["mesh"]
                mesh = tree["meshes"][mesh_no]

                deform_target = {"POSITION": deformation_shape_idx}

                # make sure the referenced bufferview contains the target property
                primitive = mesh["primitives"][0]
                if primitive.get("targets") is None:
                    primitive["targets"] = []

                target_idx = len(primitive["targets"])
                primitive["targets"].append(deform_target)

                if mesh["extras"].get("targetNames") is None:
                    mesh["extras"]["targetNames"] = []
                mesh["extras"]["targetNames"].append(self.name)

                if mesh.get("weights") is None:
                    mesh["weights"] = []

                mesh["weights"].append(target_idx)

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


@dataclass
class AnimationStore:
    animations: list[Animation] = field(default_factory=list)

    def __call__(self, buffer_items, tree, *args, **kwargs):
        for idx, animation in enumerate(self.animations):
            animation(buffer_items, tree, morph_target_index=idx, num_morph_targets=len(self.animations))

    def add(self, animation: Animation):
        self.animations.append(animation)

    @staticmethod
    def update_buffer_view(tree, accessor_idx, target_num):
        buffer_view_idx = tree["accessors"][accessor_idx]["bufferView"]
        buffer_view = tree["bufferViews"][buffer_view_idx]
        if buffer_view.get("target") is None:
            buffer_view["target"] = target_num

    @staticmethod
    def tree_postprocessor(tree):
        for material in tree["materials"]:
            material["doubleSided"] = True

        for anim in tree["animations"]:
            node_idx = anim["channels"][0]["target"]["node"]
            mesh_idx = tree["nodes"][node_idx]["mesh"]
            mesh = tree["meshes"][mesh_idx]
            for primitive in mesh["primitives"]:
                AnimationStore.update_buffer_view(tree, primitive["attributes"]["POSITION"], 34962)
                AnimationStore.update_buffer_view(tree, primitive["indices"], 34963)
                for target in primitive["targets"]:
                    AnimationStore.update_buffer_view(tree, target["POSITION"], 34962)
