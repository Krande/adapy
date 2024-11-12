from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.base.changes import ChangeAction
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada import Assembly, Beam, Part, Pipe, Plate, Shape, Wall


@dataclass
class Group:
    name: str
    members: list[Part | Beam | Plate | Wall | Pipe | Shape]
    parent: Part | Assembly
    description: str = ""
    guid: str = field(default_factory=create_guid)
    change_type: ChangeAction = ChangeAction.NOTDEFINED

    def to_part(self, name: str):
        p = Part(name)
        for mem in self.members:
            p.add_object(mem)
        return p
