from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ada import Beam, Plate


class MemberRole(Enum):
    INCOMING = "incoming"
    LANDING = "landing"


class MemberKind(Enum):
    BEAM = auto()
    PLATE = auto()

    @staticmethod
    def from_ada_type(elem: Beam | Plate) -> MemberKind:
        from ada import Beam, Plate

        if isinstance(elem, Beam):
            return MemberKind.BEAM
        if isinstance(elem, Plate):
            return MemberKind.PLATE
        raise TypeError(f"Unsupported member type: {type(elem)!r}")


@dataclass(frozen=True)
class AngleRange:
    min_deg: float
    max_deg: float

    def contains(self, value_deg: float) -> bool:
        v = ((value_deg + 180.0) % 360.0) - 180.0
        return self.min_deg <= v <= self.max_deg


@dataclass(frozen=True)
class MemberCriteria:
    role: MemberRole
    kind: MemberKind | None = None
    section_in: frozenset[str] | None = None
    angle_to_role: MemberRole | None = None
    angle_range: AngleRange | None = None
    predicate: Callable[[dict[MemberRole, Any]], bool] | None = None

    def matches_single(self, member: Beam | Plate) -> bool:
        from ada import Beam, Plate

        if self.kind is not None and MemberKind.from_ada_type(member) is not self.kind:
            return False
        if self.section_in is not None:
            allowed = {s.upper() for s in self.section_in}
            if isinstance(member, Beam):
                sec_name = member.section.type.value.upper()
                if sec_name not in allowed:
                    return False
            elif isinstance(member, Plate):
                if "PLATE" not in allowed:
                    return False
        return True


SampleSynth = Callable[
    ["ConnectionSpec", dict[str, dict[str, Any]]],
    dict[MemberRole, Any],
]


@dataclass(frozen=True)
class ConnectionSpec:
    name: str
    roles: tuple[MemberCriteria, ...]
    tags: frozenset[str] = frozenset()
    priority: int = 0
    defaults: dict[str, dict[str, Any]] | None = None
    """Optional default inputs for `build_component(spec.name, defaults)`.

    Same shape as the `inputs` arg to `build_sample`/`build_component`.
    Used by the CI bake job to pick representative inputs for each spec
    when generating preview GLBs, and by the frontend to pre-fill the
    configuration form. Specs without defaults are skipped during the
    bake."""
    synth_sample: SampleSynth | None = None
    """Optional spec-level sample-synthesis hook.

    Signature: ``(spec, inputs) -> dict[MemberRole, Beam | Plate]``.
    ``build_sample(spec, inputs)`` calls it first when present; the
    default beam-from-line logic only runs when this is None.

    Used by specs whose sample geometry can't be expressed by the
    default BEAM placement rule — e.g. a spec with a PLATE member,
    or a stub-style spec that needs to fabricate the boolean cuts
    its handler expects. Lives next to the spec declaration so the
    spec, its handler, and its sample synth stay co-located.
    """


ConnectionBuilder = Callable[..., Any]


@dataclass
class RegisteredConnection:
    spec: ConnectionSpec
    fn: ConnectionBuilder


_REGISTRY: dict[str, RegisteredConnection] = {}


def register_connection(spec: ConnectionSpec):
    def deco(fn: ConnectionBuilder) -> ConnectionBuilder:
        if spec.name in _REGISTRY:
            raise ValueError(f"Connection spec already registered: {spec.name!r}")
        _REGISTRY[spec.name] = RegisteredConnection(spec=spec, fn=fn)
        return fn

    return deco


def list_specs() -> list[ConnectionSpec]:
    return [r.spec for r in _REGISTRY.values()]


def get_spec(name: str) -> ConnectionSpec:
    return _REGISTRY[name].spec


def get_registered(name: str) -> RegisteredConnection:
    return _REGISTRY[name]


def all_registered() -> list[RegisteredConnection]:
    return list(_REGISTRY.values())


def _clear_registry() -> None:
    _REGISTRY.clear()


def spec_to_form_schema(spec: ConnectionSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "tags": sorted(spec.tags),
        "priority": spec.priority,
        "defaults": spec.defaults,
        "roles": [
            {
                "role": role.role.value,
                "kind": role.kind.name if role.kind is not None else None,
                "section_in": sorted(role.section_in) if role.section_in is not None else None,
                "angle_to_role": role.angle_to_role.value if role.angle_to_role is not None else None,
                "angle_range": (
                    {"min_deg": role.angle_range.min_deg, "max_deg": role.angle_range.max_deg}
                    if role.angle_range is not None
                    else None
                ),
                "has_predicate": role.predicate is not None,
            }
            for role in spec.roles
        ],
    }
