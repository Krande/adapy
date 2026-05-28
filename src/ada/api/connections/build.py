"""Build a Connection from a registered ConnectionSpec + user inputs.

`build_component(spec_name, inputs)` resolves the spec, synthesizes
sample members, invokes the registered handler, and wraps the result
(sample members + stiffeners + welds) into a `Connection(Part)` ready
for `.to_gltf()`.

Handler contract (duck-typed, no Protocol enforcement):

- Invoked as `handler(**{role.value: member for role, member in members.items()})`
  — so a spec with roles INCOMING / LANDING calls
  `handler(incoming=beam_a, landing=beam_b)`.
- Return value may be `None` (then the Connection contains only the
  sample members) or any object with `.welds` and `.stiffeners`
  attributes (read via `getattr` with `[]` fallback).
- Booleans / cut geometry are assumed to be attached in-place by the
  handler via `beam.add_boolean(...)` — they live on the sample
  members and ride along automatically. No separate handling here.
"""

from __future__ import annotations

from typing import Any

from ada.api.connections.joints import Connection
from ada.api.connections.sample import build_sample
from ada.api.connections.spec import get_registered


def build_component(
    spec_name: str,
    inputs: dict[str, dict[str, Any]],
    name: str | None = None,
    **extra_handler_kwargs: Any,
) -> Connection:
    """Build a Connection from a spec + inputs.

    Extra keyword arguments are forwarded to the registered handler
    alongside the role-bound sample members. Used to feed handler-
    specific context (e.g. clash data, generation config) from a
    downstream consumer without coupling adapy to those types. Keys
    must not collide with role names (`incoming`, `landing`, ...).
    """
    try:
        reg = get_registered(spec_name)
    except KeyError as e:
        raise KeyError(f"no registered connection spec named {spec_name!r}") from e

    members = build_sample(reg.spec, inputs)
    handler_kwargs = {role.value: member for role, member in members.items()}
    role_keys = set(handler_kwargs)
    collision = role_keys & set(extra_handler_kwargs)
    if collision:
        raise TypeError(
            f"extra handler kwargs collide with role names: {sorted(collision)}"
        )
    handler_kwargs.update(extra_handler_kwargs)
    result = reg.fn(**handler_kwargs)

    conn = Connection(
        name or f"{spec_name}_preview",
        spec_name=spec_name,
        spec_inputs=inputs,
    )
    for member in members.values():
        conn.add_beam(member)

    if result is not None:
        for stiffener in getattr(result, "stiffeners", None) or []:
            conn.add_plate(stiffener)
        for weld in getattr(result, "welds", None) or []:
            conn.add_weld(weld)

    return conn
