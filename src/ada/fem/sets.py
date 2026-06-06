from __future__ import annotations

from typing import TYPE_CHECKING, List, Literal, Union

import numpy as np

from ada.api.nodes import Node

from .common import FemBase

if TYPE_CHECKING:
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

    def __init__(
        self,
        name,
        members: None | list[Elem | Node],
        set_type: Literal["nset", "elset"] = None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        from ada.fem import Elem

        if members is None:
            members = []

        # Id-backed mode: members given as plain ints (resolved to node/elem proxies
        # lazily via the parent FEM). Keeps a FemSet from pinning object Node/Elem on
        # the array-backed mesh path.
        self._member_ids: list[int] | None = None
        if members and all(isinstance(m, (int, np.integer)) for m in members):
            if set_type is None:
                raise ValueError("set_type is required when members are given as ids")
            self._member_ids = [int(m) for m in members]
            self._members = None
        else:
            if set_type is None:
                set_type = eval_set_type_from_members(members)
            for m in members:
                if isinstance(m, (Elem, Node)):
                    m.refs.append(self)
            self._members = members

        self._set_type = set_type
        if self.type not in SetTypes.all:
            raise ValueError(f'set type "{set_type}" is not valid')
        self._refs = []

    def _resolve(self, mid: int):
        fem = self.parent
        return fem.nodes.from_id(mid) if self.type == SetTypes.NSET else fem.elements.from_id(mid)

    def to_id_backed(self) -> None:
        """Drop object members, keep only their ids (resolved lazily). Used when the
        owning FEM switches to the array-backed substrate so the set no longer pins
        object Node/Elem."""
        if self._member_ids is not None:
            return
        self._member_ids = [m.id for m in self._members]
        self._members = None

    def __len__(self):
        return len(self._member_ids) if self._member_ids is not None else len(self._members)

    def __contains__(self, item):
        if self._member_ids is not None:
            return item.id in self._member_ids
        return item.id in self._members

    def __getitem__(self, index):
        return self.members[index]

    def __add__(self, other: FemSet) -> FemSet:
        self.add_members(other.members)
        return self

    def add_members(self, members: List[Union[Elem, Node]]):
        if self._member_ids is not None:
            self._member_ids += [m if isinstance(m, (int, np.integer)) else m.id for m in members]
        else:
            self._members += members

    @property
    def type(self) -> str:
        return self._set_type.lower()

    @property
    def members(self) -> list[Elem | Node]:
        if self._member_ids is not None:
            return [self._resolve(i) for i in self._member_ids]
        return self._members

    @property
    def refs(self):
        return self._refs

    def __repr__(self):
        return f'FemSet({self.name}, type: "{self.type}", members: "{len(self.members)}")'


def eval_set_type_from_members(members: list[Elem | Node]) -> str:
    from ada.fem import Elem

    res = set([type(mem) for mem in members])
    if len(res) == 1 and type(members[0]) is Node:
        return FemSet.TYPES.NSET
    elif len(res) == 1 and issubclass(type(members[0]), Elem):
        return FemSet.TYPES.ELSET
    elif len(res) == 1 and type(members[0]) is tuple:
        return FemSet.TYPES.NSET
    else:
        raise ValueError("Currently Mixed Femsets are not allowed")
        # return "mixed"


def is_lazy(members: list[Elem | Node]) -> bool:
    res = set([type(mem) for mem in members])
    if len(res) == 1 and type(members[0]) is tuple:
        return True
    else:
        return False
