from dataclasses import dataclass, field

import numpy as np


class Animation:
    def __init__(
        self,
        name: str,
        ref_obj,
        keyframe_times,
        translation_keyframes=None,
        rotation_keyframes=None,
        deformation_weights_keyframes=None,
        deformation_shape=None,
        node_idx: int | list[int] = None,
    ):
        self.name = name
        self.ref_obj = ref_obj
        self.node_idx = node_idx
        self.translation_keyframes = translation_keyframes
        self.rotation_keyframes = rotation_keyframes
        self.deformation_keyframes = deformation_weights_keyframes
        self.deformation_shape = deformation_shape
        self.keyframe_times = keyframe_times

    def __call__(self, buffer_items, tree, *args, **kwargs):
        from trimesh.exchange.gltf import _data_append

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

        if self.deformation_keyframes is not None:
            deformation_shape_idx = _data_append(
                acc=tree["accessors"],
                buff=buffer_items,
                blob={"componentType": 5126, "type": "VEC3"},
                data=np.array(self.deformation_shape, dtype="float32"),
            )
            deformation_keys_idx = _data_append(
                acc=tree["accessors"],
                buff=buffer_items,
                blob={"componentType": 5126, "type": "SCALAR"},
                data=np.array(self.deformation_keyframes, dtype="float32"),
            )

            deformation_sampler = {"input": keyframe_idx, "interpolation": "LINEAR", "output": deformation_keys_idx}
            sampler_idx = len(samplers)
            samplers.append(deformation_sampler)

            for node_idx in node_idx_list:
                deformation_channel = {"sampler": sampler_idx, "target": {"node": node_idx, "path": "weights"}}
                channels.append(deformation_channel)

                # add position targets
                mesh_no = tree["nodes"][node_idx]["mesh"]
                mesh = tree["meshes"][mesh_no]

                primitive = mesh["primitives"][0]
                if primitive.get("targets") is None:
                    primitive["targets"] = []

                target_idx = len(primitive["targets"])
                primitive["targets"].append({"POSITION": deformation_shape_idx})

                mesh["extras"]["targetNames"] = [self.name]
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
        for animation in self.animations:
            animation(buffer_items, tree, *args, **kwargs)

    def add(self, animation: Animation):
        self.animations.append(animation)
