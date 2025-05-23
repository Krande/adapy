from __future__ import annotations

import reprlib
from bisect import bisect_left, insort
from itertools import chain
from operator import attrgetter
from typing import TYPE_CHECKING, Iterable

from ada.api.beams import BeamTapered
from ada.api.containers.base import NumericMapped
from ada.base.units import Units
from ada.config import logger
from ada.core.utils import Counter

if TYPE_CHECKING:
    from ada import Assembly, Part
    from ada.sections import Section


class Sections(NumericMapped):
    def __init__(self, sections: Iterable[Section] = None, parent: Part | Assembly = None, units=Units.M):
        super().__init__(parent=parent)
        self._units = units
        self._sections: list[Section] = sorted(sections or [], key=attrgetter("name"))
        self._id_map: dict[int, Section] = {}
        self._name_map: dict[str, Section] = {}
        # assign IDs and build maps
        sec_id = Counter(start=1)
        for sec in self._sections:
            if sec.id is None:
                sec.id = next(sec_id)
            self._id_map[sec.id] = sec
            self._name_map[sec.name] = sec

        if len(self._name_map) != len(self._id_map):
            names = [sec.name for sec in self._sections]
            duplicates = {n: c for n, c in Counter(names).items() if c > 1}
            logger.warning(f"The following sections are non-unique: {duplicates!r}")

    @property
    def max_id(self) -> int:
        return max(self._id_map.keys(), default=0)

    def renumber_id(self, start_id: int = 1) -> None:
        cnt = Counter(start=start_id)
        for old_id in sorted(self._id_map):
            sec = self._id_map[old_id]
            sec.id = next(cnt)
        # rebuild maps
        self._id_map = {sec.id: sec for sec in self._sections}
        self._name_map = {sec.name: sec for sec in self._sections}

    def __len__(self) -> int:
        return len(self._sections)

    def __iter__(self):
        return iter(self._sections)

    def __getitem__(self, idx):
        result = self._sections[idx]
        return Sections(result, parent=self.parent) if isinstance(idx, slice) else result

    def __add__(self, other: Sections) -> Sections:
        if self.parent is None:
            logger.error(f'Parent is None for Sections container "{self}"')
        for sec in other:
            sec.parent = self.parent
        other.renumber_id(self.max_id + 1)
        return Sections(chain(self, other), parent=self.parent)

    def __repr__(self) -> str:
        r = reprlib.Repr()
        r.maxlist = 8
        r.maxlevel = 1
        return f"{self.__class__.__name__}({r.repr(self._sections)})"

    def merge_sections_by_properties(self):
        models = []
        final_sections = []
        for i, sec in enumerate(self.sections):
            if sec not in models:
                models.append(sec)
                final_sections.append(sec)
            else:
                index = models.index(sec)
                replacement_sec = models[index].parent
                for ref in sec.refs:
                    ref.section = replacement_sec

        self._sections = final_sections
        self.recreate_name_and_id_maps(self._sections)

    def index(self, item):
        index = bisect_left(self._sections, item)
        if (index != len(self._sections)) and (self._sections[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item: Section) -> int:
        return int(item in self)

    def get_by_name(self, name: str) -> Section:
        if name not in self._name_map.keys():
            raise ValueError(f'The section id "{name}" is not found')

        return self._name_map[name]

    def get_by_id(self, sec_id: int) -> Section:
        if sec_id not in self._id_map.keys():
            raise ValueError(f'The node id "{sec_id}" is not found')

        return self._id_map[sec_id]

    @property
    def sections(self) -> list[Section]:
        return self._sections

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, new_units):
        if isinstance(new_units, str):
            new_units = Units.from_str(new_units)
        if new_units != self._units:
            for sec in self._sections:
                sec.units = new_units
            self._units = new_units

    @property
    def id_map(self) -> dict[int, Section]:
        return self._id_map

    @property
    def name_map(self) -> dict[str, Section]:
        return self._name_map

    def add(self, section: Section) -> Section:
        if section.name is None:
            raise ValueError("Section.name may not be None")

        # ensure correct parent
        section.parent = section.parent or self.parent

        # quick lookup by name
        if section.name in self._name_map:
            existing = self._name_map[section.name]
            # dedupe refs using a set
            existing_refs = set(existing.refs)
            for ref in section.refs:
                # redirect the ref to the existing section
                if isinstance(ref, BeamTapered):
                    if existing.equal_props(ref.section):
                        ref.section = existing
                    elif existing.equal_props(ref.taper):
                        ref.taper = existing
                else:
                    if existing.equal_props(ref.section):
                        ref.section = existing
                # append only new refs
                if ref not in existing_refs:
                    existing.refs.append(ref)
                    existing_refs.add(ref)
            return existing

        # assign a fresh unique id
        if section.id is None or section.id in self._id_map:
            section.id = self.max_id + 1

        # insert into sorted list by name
        insort(self._sections, section, key=attrgetter("name"))

        # update maps
        self._id_map[section.id] = section
        self._name_map[section.name] = section

        return section
