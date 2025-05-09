from __future__ import annotations

from dataclasses import dataclass, field
from typing import OrderedDict

from ada.api.animations import Animation


@dataclass
class GltfPostProcessor:
    animations: list[Animation] = field(default_factory=list)
    extensions: dict = field(default_factory=dict)

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
        for anim in tree["animations"]:
            node_idx = anim["channels"][0]["target"]["node"]
            mesh_idx = tree["nodes"][node_idx]["mesh"]
            mesh = tree["meshes"][mesh_idx]
            for primitive in mesh["primitives"]:
                self._update_buffer_view(tree, primitive["attributes"]["POSITION"], 34962)
                self._update_buffer_view(tree, primitive["indices"], 34963)
                for target in primitive["targets"]:
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
            animation(buffer_items, tree, morph_target_index=idx, num_morph_targets=len(self.animations))

    def tree_postprocessor(self, tree: OrderedDict):
        for material in tree["materials"]:
            material["doubleSided"] = True

        self._update_animations(tree)
        self._update_extensions(tree)
