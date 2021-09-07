from __future__ import annotations

from typing import List, Union

from ada.concepts.points import Node

from .common import FemBase
from .elements import Elem


class SetTypes:
    NSET = "nset"
    ELSET = "elset"

    all = [NSET, ELSET]


class FemSet(FemBase):
    """

    :param name: Name of Set
    :param members: Set Members
    :param set_type: Type of set (either 'nset' or 'elset')
    :param metadata: Metadata for object
    :param parent: Parent object
    """

    TYPES = SetTypes

    def __init__(self, name, members, set_type, metadata=None, parent=None):
        super().__init__(name, metadata, parent)
        self._set_type = set_type
        if self.type not in SetTypes.all:
            raise ValueError(f'set type "{set_type}" is not valid')
        self._members = members

    def __len__(self):
        return len(self._members)

    def __contains__(self, item):
        return item.id in self._members

    def __getitem__(self, index):
        return self._members[index]

    def __add__(self, other: FemSet) -> FemSet:
        self.add_members(other.members)
        return self

    def add_members(self, members: List[Union[Elem, Node]]):
        self._members += members

    @property
    def type(self):
        return self._set_type.lower()

    @property
    def members(self) -> List[Union[Elem, Node]]:
        return self._members

    def __repr__(self):
        return f'FemSet({self.name}, type: "{self.type}", members: "{len(self.members)}")'
