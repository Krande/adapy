import numpy as np


class Animate:
    def __init__(self, name: str, translation_keyframes, keyframe_times, rotation_keyframes=None, node_idx: int = None):
        self.name = name
        self.node_idx = node_idx
        self.translation_keyframes = translation_keyframes
        self.rotation_keyframes = rotation_keyframes
        self.keyframe_times = keyframe_times

    def __call__(self, buffer_items, tree, *args, **kwargs):
        from trimesh.exchange.gltf import _data_append

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
            translation_channel = {"sampler": sampler_idx, "target": {"node": self.node_idx, "path": "translation"}}
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
            rotation_channel = {"sampler": sampler_idx, "target": {"node": self.node_idx, "path": "rotation"}}
            channels.append(rotation_channel)

        # Update tree with data added to buffer
        tree["animations"] = [
            {
                "name": self.name,
                "samplers": samplers,
                "channels": channels,
            }
        ]
