"""Visual effects as plain data: what a scene asks a viewer to show beyond its geometry.

An :class:`EffectSpec` holds:

* point lights that can flicker;
* particle emitters (sparks, smoke);
* emissive ramps (a part glowing and cooling down over time);
* the look settings that go with them: an HDR target with tone mapping and bloom, and image-based
  lighting.

It travels inside a GLB, in the default scene's ``extras`` under :data:`EXTRAS_KEY`. Any viewer
can read it from there; ADA Studio's native (adacpp WebGPU) viewport draws it. The keys are the
ones the adacpp renderer reads (adacpp ``docs/render_effects.md``), and so are the defaults: a
field left as ``None`` is not written, and the viewer uses its own default for it.

Times are seconds on the scene's timeline. Every effect is a pure function of that time, so a
paused, scrubbed or replayed timeline shows the same thing at the same time. Positions,
directions and gravity are in the GLB's coordinates. Colours are linear RGB.

This module is stdlib-only: the slim viewer image ships it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Callable, Literal, Optional

EXTRAS_KEY = "ada_effects"
SCHEMA_VERSION = 1

Vec3 = tuple[float, float, float]
ToneMapping = Literal["none", "neutral", "aces"]
ParticleBlend = Literal["additive", "alpha"]
EmitterPreset = Literal["sparks", "smoke"]

_TONE_MAPPINGS = ("none", "neutral", "aces")
_BLENDS = ("additive", "alpha")
_PRESETS = ("sparks", "smoke")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.title() for p in rest)


def _snake(name: str) -> str:
    return "".join("_" + c.lower() if c.isupper() else c for c in name)


class _Plain:
    """``to_dict`` / ``from_dict`` for a flat dataclass, with camelCase keys and ``None`` left out."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            if value is None:
                continue
            if isinstance(value, tuple):
                value = [float(v) for v in value]
            out[_camel(f.name)] = value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        kwargs = {}
        for key, value in data.items():
            name = _snake(key)
            if name not in names:
                continue  # a newer writer's key: ignored, as the renderer ignores it
            if isinstance(value, list):
                value = tuple(value)
            kwargs[name] = value
        return cls(**kwargs)


def _object_id(target: Any) -> str:
    """An object's id as the GLB names it: an adapy object's guid, or the id given as text."""
    guid = getattr(target, "guid", None)
    return str(guid if guid is not None else target)


def _check_vec3(name: str, value: Optional[Vec3]) -> None:
    if value is not None and len(value) != 3:
        raise ValueError(f"{name} must have 3 components, got {value!r}")


@dataclass
class PostSettings(_Plain):
    """HDR target, tone mapping and bloom.

    These settings are applied only when the viewer is asked to take the scene's own look.
    """

    hdr: Optional[bool] = True
    tone_mapping: Optional[ToneMapping] = None
    exposure: Optional[float] = None
    bloom: Optional[bool] = None
    bloom_threshold: Optional[float] = None
    bloom_knee: Optional[float] = None
    bloom_strength: Optional[float] = None
    bloom_radius: Optional[float] = None

    def __post_init__(self):
        if self.tone_mapping is not None and self.tone_mapping not in _TONE_MAPPINGS:
            raise ValueError(f"tone_mapping must be one of {_TONE_MAPPINGS}, got {self.tone_mapping!r}")


@dataclass
class EnvironmentSettings(_Plain):
    """Image-based lighting from the viewer's environment.

    The default environment is a procedural studio. ``rotation`` is in radians about ``up``.
    """

    enabled: Optional[bool] = True
    intensity: Optional[float] = None
    rotation: Optional[float] = None
    up: Optional[Vec3] = None

    def __post_init__(self):
        _check_vec3("up", self.up)


@dataclass
class PointLight(_Plain):
    """A point light, as three.js' PointLight.

    * ``intensity`` is in candela.
    * ``range`` is where the light falls to zero; 0 means no cutoff.
    * ``flicker`` (0..1) modulates the intensity with seeded smooth noise whose fastest variation
      is ``flicker_rate`` Hz.
    * The light is on between ``start`` and ``end``.
    """

    position: Vec3 = (0.0, 0.0, 0.0)
    color: Optional[Vec3] = None
    intensity: Optional[float] = None
    range: Optional[float] = None
    decay: Optional[float] = None
    flicker: Optional[float] = None
    flicker_rate: Optional[float] = None
    seed: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None
    enabled: Optional[bool] = None

    def __post_init__(self):
        _check_vec3("position", self.position)
        _check_vec3("color", self.color)
        if self.flicker is not None and self.flicker < 0:
            raise ValueError("flicker must not be negative")


@dataclass
class Emitter(_Plain):
    """A particle emitter: camera-facing sprites born at ``rate`` per second.

    Particles are born from ``start`` to ``end`` at ``origin`` (which moves at ``velocity``). Each
    flies into a cone of half-angle ``spread`` (radians) around ``direction``, under ``gravity``
    and linear ``drag``, and lives ``lifetime`` seconds.

    ``preset`` "sparks" (the default) or "smoke" picks the defaults; any field set here overrides
    them. ``blend`` "additive" is light; "alpha" is soft sprites.
    """

    origin: Vec3 = (0.0, 0.0, 0.0)
    preset: Optional[EmitterPreset] = None
    velocity: Optional[Vec3] = None
    direction: Optional[Vec3] = None
    spread: Optional[float] = None
    speed: Optional[float] = None
    speed_jitter: Optional[float] = None
    rate: Optional[float] = None
    lifetime: Optional[float] = None
    lifetime_jitter: Optional[float] = None
    start: Optional[float] = None
    end: Optional[float] = None
    gravity: Optional[Vec3] = None
    drag: Optional[float] = None
    radius: Optional[float] = None
    size_start: Optional[float] = None
    size_end: Optional[float] = None
    color_start: Optional[Vec3] = None
    intensity_start: Optional[float] = None
    alpha_start: Optional[float] = None
    color_end: Optional[Vec3] = None
    intensity_end: Optional[float] = None
    alpha_end: Optional[float] = None
    stretch: Optional[float] = None
    blend: Optional[ParticleBlend] = None
    seed: Optional[int] = None
    enabled: Optional[bool] = None

    def __post_init__(self):
        for name in ("origin", "velocity", "direction", "gravity", "color_start", "color_end"):
            _check_vec3(name, getattr(self, name))
        if self.preset is not None and self.preset not in _PRESETS:
            raise ValueError(f"preset must be one of {_PRESETS}, got {self.preset!r}")
        if self.blend is not None and self.blend not in _BLENDS:
            raise ValueError(f"blend must be one of {_BLENDS}, got {self.blend!r}")
        if self.rate is not None and self.rate < 0:
            raise ValueError("rate must not be negative")


@dataclass
class EmissiveRamp(_Plain):
    """A glow on some objects of the model that goes from hot to cool.

    The ramp runs over ``duration`` seconds from ``start``. ``targets`` are object ids, as the
    GLB's scene extras name them (adapy's ``id_hierarchy``). Each target takes everything below
    it, too.

    Colour and intensity are mixed by ((t - start) / duration) ** curve. A ``curve`` below 1
    cools fast at first. A zero ``duration`` glows hot all the time.
    """

    targets: list[Any] = field(default_factory=list)
    hot_color: Optional[Vec3] = None
    hot_intensity: Optional[float] = None
    cool_color: Optional[Vec3] = None
    cool_intensity: Optional[float] = None
    start: Optional[float] = None
    duration: Optional[float] = None
    curve: Optional[float] = None

    def __post_init__(self):
        _check_vec3("hot_color", self.hot_color)
        _check_vec3("cool_color", self.cool_color)
        self.targets = [_object_id(t) for t in self.targets]

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out["targets"] = list(self.targets)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        obj = super().from_dict({k: v for k, v in data.items() if k != "targets"})
        obj.targets = [str(t) for t in data.get("targets", [])]
        return obj


@dataclass
class EffectSpec:
    """Every effect of a scene, and optionally the look that goes with them.

    Writes into, and reads back from, a GLB's scene extras.
    """

    lights: list[PointLight] = field(default_factory=list)
    emitters: list[Emitter] = field(default_factory=list)
    emissive: list[EmissiveRamp] = field(default_factory=list)
    post: Optional[PostSettings] = None
    environment: Optional[EnvironmentSettings] = None

    def is_empty(self) -> bool:
        return not (self.lights or self.emitters or self.emissive or self.post or self.environment)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"version": SCHEMA_VERSION}
        if self.post is not None:
            out["post"] = self.post.to_dict()
        if self.environment is not None:
            out["environment"] = self.environment.to_dict()
        if self.lights:
            out["lights"] = [light.to_dict() for light in self.lights]
        if self.emitters:
            out["emitters"] = [e.to_dict() for e in self.emitters]
        if self.emissive:
            out["emissive"] = [r.to_dict() for r in self.emissive]
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EffectSpec":
        version = data.get("version", SCHEMA_VERSION)
        if not isinstance(version, int) or version > SCHEMA_VERSION:
            raise ValueError(f"unsupported {EXTRAS_KEY} version {version!r} (this adapy reads {SCHEMA_VERSION})")
        return cls(
            lights=[PointLight.from_dict(d) for d in data.get("lights", [])],
            emitters=[Emitter.from_dict(d) for d in data.get("emitters", [])],
            emissive=[EmissiveRamp.from_dict(d) for d in data.get("emissive", [])],
            post=PostSettings.from_dict(data["post"]) if "post" in data else None,
            environment=EnvironmentSettings.from_dict(data["environment"]) if "environment" in data else None,
        )

    def write_to_gltf_tree(self, tree: dict, resolve: Optional[Callable[[str], Optional[str]]] = None) -> None:
        """Puts the spec into the glTF JSON tree's default scene extras (a GLB tree post-processor).

        ``resolve`` maps an emissive target (an object guid, say) to the id the GLB's object
        hierarchy gives that object (``id_hierarchy``'s keys); a target it returns None for is
        written as it is.
        """
        data = self.to_dict()
        if resolve is not None:
            for ramp in data.get("emissive", []):
                ramp["targets"] = [resolve(t) or t for t in ramp["targets"]]
        scenes = tree.setdefault("scenes", [{}])
        if not scenes:
            scenes.append({})
        scene = scenes[tree.get("scene", 0) or 0]
        extras = scene.get("extras") or {}
        extras[EXTRAS_KEY] = data
        scene["extras"] = extras

    @classmethod
    def from_gltf_tree(cls, tree: dict) -> Optional["EffectSpec"]:
        """The spec a glTF JSON tree carries in its default scene extras, if any."""
        scenes = tree.get("scenes") or []
        index = tree.get("scene", 0) or 0
        if index >= len(scenes):
            return None
        data = (scenes[index].get("extras") or {}).get(EXTRAS_KEY)
        return cls.from_dict(data) if isinstance(data, dict) else None


__all__ = [
    "EXTRAS_KEY",
    "SCHEMA_VERSION",
    "EffectSpec",
    "EmissiveRamp",
    "Emitter",
    "EnvironmentSettings",
    "PointLight",
    "PostSettings",
]
