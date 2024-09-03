from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada import Assembly


@dataclass
class CacheStore:
    name: str
    cache_dir: pathlib.Path | None = None
    state_file: pathlib.Path = field(default=None)
    cache_file: pathlib.Path = field(default=None)

    _cache_loaded: bool = False

    def __post_init__(self):
        if self.cache_dir is None:
            self.cache_dir = pathlib.Path("").parent.resolve().absolute() / ".state"
        if isinstance(self.cache_dir, str):
            self.cache_dir = pathlib.Path(self.cache_dir)
        state_path = self.cache_dir / self.name
        self.state_file = state_path.with_suffix(".json")
        self.cache_file = state_path.with_suffix(".h5")

    def sync(self, assembly: Assembly, clear_cache=False):
        if self.cache_file.exists() and clear_cache:
            os.remove(self.cache_file)
        if self.state_file.exists() and clear_cache:
            os.remove(self.state_file)

        self._cache_loaded = False
        self.from_cache(assembly)

    def from_cache(self, assembly: Assembly, input_file=None):
        is_cache_outdated = self.is_cache_outdated(input_file)
        if input_file is None and is_cache_outdated is False:
            self.read_cache(assembly)
            return True

        if is_cache_outdated is False and self._cache_loaded is False:
            self.read_cache(assembly)
            return True
        elif is_cache_outdated is False and self._cache_loaded is True:
            return True
        else:
            return False

    def _get_file_state(self):
        state_file = self.state_file
        if state_file.exists() is True:
            with open(state_file, "r") as f:
                state = json.load(f)
                return state
        return dict()

    def _update_file_state(self, input_file=None):
        in_file = pathlib.Path(input_file)
        fna = in_file.name
        last_modified = os.path.getmtime(in_file)
        state_file = self.state_file
        state = self._get_file_state()

        state.get(fna, dict())
        state[fna] = dict(lm=last_modified, fp=str(in_file))

        os.makedirs(state_file.parent, exist_ok=True)

        with open(state_file, "w") as f:
            json.dump(state, f, indent=4)

    def to_cache(self, assembly: Assembly, input_file, write_to_cache: bool):
        self._update_file_state(input_file)
        if write_to_cache:
            self.update_cache(assembly)

    def read_cache(self, assembly: Assembly):
        from ada.cache.reader import read_assembly_from_cache

        read_assembly_from_cache(self.cache_file, assembly)
        print(f"Finished Loading model from cache {self.cache_file}")

    def update_cache(self, assembly: Assembly):
        from ada.cache.writer import write_assembly_to_cache

        write_assembly_to_cache(assembly, self.cache_file)

    def is_cache_outdated(self, input_file=None):
        is_cache_outdated = False
        state = self._get_file_state()

        for name, props in state.items():
            in_file = pathlib.Path(props.get("fp"))
            last_modified_state = props.get("lm")
            if in_file.exists() is False:
                is_cache_outdated = True
                break

            last_modified = os.path.getmtime(in_file)
            if last_modified != last_modified_state:
                is_cache_outdated = True
                break

        if self.cache_file.exists() is False:
            logger.debug("Cache file not found")
            is_cache_outdated = True

        if input_file is not None:
            curr_in_file = pathlib.Path(input_file)
            if curr_in_file.name not in state.keys():
                is_cache_outdated = True

        return is_cache_outdated
