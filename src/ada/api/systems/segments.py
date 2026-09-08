"""System segments: the individual runs a :class:`~.base.System` is made of.

A ``System`` routes as a single run between its first and last port, and the
routed geometry carries nothing about what sits *along* that run. A
``SystemSegment`` records exactly that: the two ends and the in-line components
(valves, reducers, orifices, …) between them, as the source definition had
them. Nothing in routing reads them — they are carried so a system imported
from a process definition can be written back out with its detail intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ports import Port

__all__ = ["SystemSegment"]


@dataclass
class SystemSegment:
    name: str
    from_port: Port | None = None
    to_port: Port | None = None
    #: In-line components along the run, each an untyped dict so a producer can
    #: carry whatever its source describes (class, tag, order, attributes).
    components: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
