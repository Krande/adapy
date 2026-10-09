"""Lazy load combinations: materialise a combination case from the baked stored cases.

A load combination is ``sum(factor * stored case)``. The base bake writes the mesh
and the STORED cases only; a combination is materialised here when someone asks
for it, and cached as an overlay of single-step blobs next to the base bake.

This module is the reference implementation other engines (a native or WASM
kernel) are compared against, so its arithmetic is specified exactly below and
in :data:`DERIVATION_OPS`. Nothing here is format specific: it reads the base
manifest and the base blobs' strides, never a solver file.

Tier A -- superposition of baked strides
========================================

A stride is one step of one blob: ``n_points x n_comp`` (AFBL) or
``n_elements x n_ips x n_comp`` (AFEL) float32, little-endian, C order. For a
combination with terms ``(basic, F, phi)`` in FILE ORDER (the RDRESCMB order,
as ``combination_steps[].terms`` lists them):

1. the coefficient of each term is ``c = float32(F * cos(phi))``, formed in
   double from the widened float32 ``F`` and ``phi`` and rounded to float32 once
   (``combination_steps[].coefficients[k][0]``; the manifest carries it so no
   engine has to reproduce a libm ``cos``);
2. ``out = x_1 * c_1`` (float32 multiply, one rounding);
3. for every further term, ``tmp = x_k * c_k`` (float32, one rounding) and then
   ``out = out + tmp`` (float32, one rounding) -- two separate roundings, no
   fused multiply-add;
4. every component of the stride is superposed this way, then each component the
   field declares DERIVED (``derived_components``) is overwritten by its
   derivation op evaluated on the superposed inputs.

That is the eager superposition's own arithmetic (``read_sin._accumulate_rv_combination``):
it combines the solver's raw float32 words the same way before anything is derived
from them, so a component the bake writes as a raw word (displacements, reactions,
result-point stresses and forces) comes out bit-identical. A component the bake
forms in double before it rounds (an average, a membrane/bending split, a stress
resultant, a beam stress) is linear too, but superposing its rounded values
differs from rounding its superposition by a few float32 ULP.

A term whose basic case is complex contributes ``R cos phi - I sin phi``. The
base bake keeps only ``R`` (a complex case is presented at phase 0), so a
recipe with a complex basic case at a non-zero ``s = float32(F * sin(phi))``
cannot be done here: ``needs_raw``, and the caller materialises it from the
source records instead (:func:`materialise_case_from_source`).

The single-step blobs a case writes are the bake's own format: the same 1 KB
header (``n_steps`` 1) and the same filenames as the base blobs, under
``cases/<n>-<recipeHash8>/`` beside the base manifest, with the per-step
``scalar_range`` the bake would have computed and a ``fea.case.json`` overlay
describing them (see :func:`case_overlay`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from .fields import ElementFieldBlobWriter, FieldBlobWriter
from .formats import FEA_BAKE_VERSION
from .specs import ElementFieldSpec, ElementStepValues, FieldSpec, StepValues

#: Version of the lazy-case contract: the ``combination_steps`` /
#: ``lazy_cases`` / ``linear_components`` / ``derived_components`` manifest keys
#: and the ``fea.case.json`` overlay.
LAZY_CASES_VERSION = 1

#: Where materialised cases live, relative to the base manifest's directory.
CASES_PREFIX = "cases/"

#: The overlay's filename inside a case directory.
CASE_OVERLAY_NAME = "fea.case.json"

#: The engine stamp this module writes into ``producer``.
ENGINE = "numpy"


# ---------------------------------------------------------------------------
# Derivation ops
# ---------------------------------------------------------------------------
#
# Every op takes float32 input arrays, widens them to float64, evaluates the
# formula in float64 in exactly the order written (left to right, no
# re-association, no FMA), and rounds the result to float32 once. The formulas
# are those of ``ada.fem.formats.sesam.results.derived_values`` and of the
# nodal displacement magnitude, which is what the bake writes; a test holds
# them equal.


def _f64(a) -> np.ndarray:
    return np.asarray(a, dtype=np.float32).astype(np.float64)


def op_magnitude3(a, b, c) -> np.ndarray:
    """``sqrt((a*a + b*b) + c*c)``."""
    a, b, c = _f64(a), _f64(b), _f64(c)
    return np.sqrt((a * a + b * b) + c * c).astype(np.float32)


def op_plane_von_mises(sx, sy, txy) -> np.ndarray:
    """``sqrt(((sx*sx + sy*sy) - sx*sy) + (3.0*txy)*txy)``."""
    sx, sy, txy = _f64(sx), _f64(sy), _f64(txy)
    return np.sqrt(((sx * sx + sy * sy) - sx * sy) + (3.0 * txy) * txy).astype(np.float32)


def _principal(sx, sy, txy):
    sx, sy, txy = _f64(sx), _f64(sy), _f64(txy)
    centre = 0.5 * (sx + sy)
    half = 0.5 * (sx - sy)
    radius = np.sqrt(half * half + txy * txy)
    return centre, radius


def op_plane_principal_1(sx, sy, txy) -> np.ndarray:
    """``0.5*(sx+sy) + sqrt((0.5*(sx-sy))*(0.5*(sx-sy)) + txy*txy)``."""
    centre, radius = _principal(sx, sy, txy)
    return (centre + radius).astype(np.float32)


def op_plane_principal_2(sx, sy, txy) -> np.ndarray:
    """``0.5*(sx+sy) - sqrt((0.5*(sx-sy))*(0.5*(sx-sy)) + txy*txy)``."""
    centre, radius = _principal(sx, sy, txy)
    return (centre - radius).astype(np.float32)


#: The derivation ops, machine readable: name -> number of inputs, the formula
#: (float64, evaluated as parenthesised, result rounded to float32 once) and the
#: bake code it reproduces. A field's ``derived_components`` names one of these
#: per derived component, with ``args`` (component names, in this order) and an
#: optional ``field`` (the field the args are read from when it is not the
#: field itself; same support, element type and entity order).
DERIVATION_OPS: dict[str, dict] = {
    "magnitude3": {
        "inputs": 3,
        "formula": "sqrt((a*a + b*b) + c*c)",
        "reference": "numpy.linalg.norm(values[:, :3], axis=1) in sesam.derived_fields.build_nodal_kinematics",
        "impl": op_magnitude3,
    },
    "plane_von_mises": {
        "inputs": 3,
        "formula": "sqrt(((sx*sx + sy*sy) - sx*sy) + (3.0*txy)*txy)",
        "reference": "sesam.derived_values.plane_von_mises",
        "impl": op_plane_von_mises,
    },
    "plane_principal_1": {
        "inputs": 3,
        "formula": "0.5*(sx+sy) + sqrt((0.5*(sx-sy))*(0.5*(sx-sy)) + txy*txy)",
        "reference": "sesam.derived_values.plane_principal[..., 0]",
        "impl": op_plane_principal_1,
    },
    "plane_principal_2": {
        "inputs": 3,
        "formula": "0.5*(sx+sy) - sqrt((0.5*(sx-sy))*(0.5*(sx-sy)) + txy*txy)",
        "reference": "sesam.derived_values.plane_principal[..., 1]",
        "impl": op_plane_principal_2,
    },
}


def derivation_ops_table() -> dict[str, dict]:
    """:data:`DERIVATION_OPS` without the Python callables (JSON-safe)."""
    return {name: {k: v for k, v in spec.items() if k != "impl"} for name, spec in DERIVATION_OPS.items()}


# ---------------------------------------------------------------------------
# Which components of a field superpose, and how the others are derived
# ---------------------------------------------------------------------------

# Attribute (the last element of a field's ``group_path``) -> the derived
# components and their ops. Every other component of a field with a known
# attribute is linear. ``source`` names the attribute whose field (at the same
# position and surface) supplies the args, when not the field itself.
_ATTRIBUTE_RULES: dict[str, dict[str, dict]] = {
    "DISPLACEMENT": {"ALL": {"op": "magnitude3", "args": ["X", "Y", "Z"]}},
    "REACTION-FORCE": {},
    "G-STRESS": {"VONMISES": {"op": "plane_von_mises", "args": ["SIGXX", "SIGYY", "TAUXY"]}},
    "P-STRESS": {
        "P1": {"op": "plane_principal_1", "args": ["SIGXX", "SIGYY", "TAUXY"], "source": "G-STRESS"},
        "P2": {"op": "plane_principal_2", "args": ["SIGXX", "SIGYY", "TAUXY"], "source": "G-STRESS"},
    },
    "D-STRESS": {"MVONMISES": {"op": "plane_von_mises", "args": ["SIGMX", "SIGMY", "TAUMXY"]}},
    "PM-STRESS": {
        "P1": {"op": "plane_principal_1", "args": ["SIGMX", "SIGMY", "TAUMXY"], "source": "D-STRESS"},
        "P2": {"op": "plane_principal_2", "args": ["SIGMX", "SIGMY", "TAUMXY"], "source": "D-STRESS"},
    },
    "R-STRESS": {},
    "G-FORCE": {},
    "B-STRESS": {},
}

# Fields known by name rather than by presentation: the raw solver tables a bake
# keeps when no semantic field supersedes them. Their words are the solver's.
_LINEAR_BY_NAME = {"RVNODDIS", "REACTION-FORCE", "STRESS", "FORCES"}


def _attribute(field: Mapping) -> str | None:
    path = field.get("group_path") or []
    return str(path[-1]) if path else None


def classify_fields(fields: Sequence[Mapping]) -> dict[str, dict]:
    """``{field name: {"linear_components": [...], "derived_components": {...}}}``
    for every field of a manifest that a combination can be superposed for.

    A field left out (a property field, a field of unknown meaning, a derived
    component whose source field is missing) cannot be materialised by Tier A.
    """
    by_key: dict[tuple, str] = {}
    for f in fields:
        path = f.get("group_path") or []
        if len(path) >= 2:
            by_key[(str(path[0]), str(path[-1]), f.get("surface") or "")] = f["name_canonical"]
    out: dict[str, dict] = {}
    for f in fields:
        if f.get("category") == "property":
            continue
        name = f["name_canonical"]
        comps = list(f.get("components") or [])
        attr = _attribute(f)
        if attr in _ATTRIBUTE_RULES:
            rules = _ATTRIBUTE_RULES[attr]
        elif name in _LINEAR_BY_NAME and not f.get("group_path"):
            rules = {}
        else:
            continue
        derived: dict[str, dict] = {}
        ok = True
        for comp, rule in rules.items():
            if comp not in comps:
                continue
            entry = {"op": rule["op"], "args": list(rule["args"])}
            if "source" in rule:
                path = f.get("group_path") or []
                source = by_key.get((str(path[0]) if path else "", rule["source"], f.get("surface") or ""))
                if source is None:
                    ok = False
                    break
                entry["field"] = source
            derived[comp] = entry
        if not ok:
            continue
        out[name] = {
            "linear_components": [c for c in comps if c not in derived],
            "derived_components": derived,
        }
    return out


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


def _f32_bits(x: float) -> int:
    return int(np.float32(x).view(np.uint32))


def recipe_hash(n: int, complex_: bool, terms: Iterable[Sequence[float]]) -> str:
    """sha256 (hex) of a combination recipe, the cache key of its materialised case.

    The hashed bytes, little-endian with no padding: ``int64 n``, ``uint8
    complex``, ``uint32 n_terms``, then per term in file order ``int64 basic``,
    ``uint32 float32-bits(factor)``, ``uint32 float32-bits(phase)``.
    """
    terms = [tuple(t) for t in terms]
    h = hashlib.sha256()
    h.update(struct.pack("<qBI", int(n), 1 if complex_ else 0, len(terms)))
    for basic, factor, phase in terms:
        h.update(struct.pack("<qII", int(basic), _f32_bits(factor), _f32_bits(phase)))
    return h.hexdigest()


def term_coefficients(factor: float, phase: float) -> tuple[float, float]:
    """``(float32(F cos phi), float32(F sin phi))`` -- the eager superposition's rounding."""
    return float(np.float32(factor * math.cos(phase))), float(np.float32(factor * math.sin(phase)))


def combination_entry(
    n: int,
    complex_: bool,
    terms: Sequence[Sequence[float]],
    *,
    complex_basics: Iterable[int],
    baked: Iterable[int | float],
    name: str | None = None,
    makeup: str | None = None,
) -> dict:
    """One ``combination_steps`` entry of the manifest."""
    complex_basics = {int(b) for b in complex_basics}
    baked_set = {float(v) for v in baked}
    terms = [(int(b), float(f), float(p)) for b, f, p in terms]
    coefficients = [term_coefficients(f, p) for _, f, p in terms]
    reasons = []
    for (basic, _, _), (_, s) in zip(terms, coefficients):
        if float(basic) not in baked_set:
            reasons.append(f"basic case {basic} is not baked")
        elif basic in complex_basics and s != 0.0:
            reasons.append(f"complex basic case {basic} at a non-zero phase")
    entry: dict = {"n": int(n)}
    if name:
        entry["name"] = str(name)
    if makeup:
        entry["makeup"] = str(makeup)
    entry.update(
        {
            "complex": bool(complex_),
            "terms": [[b, f, p] for b, f, p in terms],
            "coefficients": [[c, s] for c, s in coefficients],
            "needs_raw": bool(reasons),
            "recipe_hash": recipe_hash(n, complex_, terms),
        }
    )
    if reasons:
        entry["raw_reason"] = "; ".join(dict.fromkeys(reasons))
    return entry


def case_dir_name(entry: Mapping) -> str:
    """``<n>-<recipeHash8>``, the case's directory under :data:`CASES_PREFIX`."""
    return f"{int(entry['n'])}-{str(entry['recipe_hash'])[:8]}"


def find_combination(manifest: Mapping, case: int | Mapping) -> dict:
    if isinstance(case, Mapping):
        return dict(case)
    for entry in manifest.get("combination_steps") or []:
        if int(entry["n"]) == int(case):
            return dict(entry)
    raise KeyError(f"case {case} is not a lazy combination of this bake")


# ---------------------------------------------------------------------------
# Tier A
# ---------------------------------------------------------------------------


class NeedsRawRecords(RuntimeError):
    """The case cannot be superposed from the baked strides; read the source records."""


#: ``fetch_stride(blob, step_index)``: one stride of a base blob -- ``blob`` is
#: the manifest's blob dict (``url``, ``header_bytes``, ``stride_bytes``), the
#: bytes wanted ``[header_bytes + i*stride_bytes, + stride_bytes)``.
StrideFetcher = Callable[[Mapping, int], "bytes | np.ndarray"]


def superpose(strides: Sequence[np.ndarray], coefficients: Sequence[float]) -> np.ndarray:
    """``x_1*c_1 (+ x_k*c_k)...`` in float32, in the order given (see the module docstring)."""
    if not strides:
        raise ValueError("a combination needs at least one term")
    out = np.asarray(strides[0], dtype=np.float32) * np.float32(coefficients[0])
    for x, c in zip(strides[1:], coefficients[1:]):
        tmp = np.asarray(x, dtype=np.float32) * np.float32(c)
        out += tmp
    return out


def _as_f32(raw, n_values: int) -> np.ndarray:
    arr = np.frombuffer(raw, dtype="<f4") if isinstance(raw, (bytes, bytearray, memoryview)) else np.asarray(raw)
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    if arr.size != n_values:
        raise ValueError(f"stride holds {arr.size} values, expected {n_values}")
    return arr


@dataclass
class _Bucket:
    """One blob of a field: the nodal blob, or one element type's AFEL blob."""

    field: str
    elem_type: str | None
    blob: dict
    shape: tuple[int, ...]
    components: list[str]
    meta: dict  # the manifest's per_type entry, or the field entry


def _buckets(manifest: Mapping, field: Mapping) -> list[_Bucket]:
    comps = list(field["components"])
    if field.get("per_type"):
        return [
            _Bucket(
                field["name_canonical"],
                pt["elem_type"],
                dict(pt["blob"]),
                (int(pt["n_elements"]), int(pt["n_ips"]), len(comps)),
                comps,
                pt,
            )
            for pt in field["per_type"]
        ]
    n_points = int(field["blob"]["stride_bytes"]) // (4 * len(comps))
    return [_Bucket(field["name_canonical"], None, dict(field["blob"]), (n_points, len(comps)), comps, field)]


def _step_index(field: Mapping, value: int | float) -> int:
    for step in field.get("steps") or []:
        if float(step["value"]) == float(value):
            return int(step["i"])
    raise NeedsRawRecords(f"field {field['name_canonical']!r} has no baked step {value:g}")


def _field_order(names: Iterable[str], classes: Mapping[str, dict]) -> list[str]:
    """``names`` plus the fields their derivations read, sources first."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for rule in (classes.get(name) or {}).get("derived_components", {}).values():
            if rule.get("field"):
                visit(rule["field"])
        order.append(name)

    for name in names:
        visit(name)
    return order


def _field_classes(manifest: Mapping) -> dict[str, dict]:
    """Per-field classes from the manifest (``linear_components`` /
    ``derived_components``), falling back to :func:`classify_fields` for a
    manifest written before the keys existed."""
    fields = manifest.get("fields") or []
    stamped = {
        f["name_canonical"]: {
            "linear_components": list(f["linear_components"]),
            "derived_components": dict(f.get("derived_components") or {}),
        }
        for f in fields
        if "linear_components" in f
    }
    return stamped or classify_fields(fields)


def case_fields(manifest: Mapping) -> list[str]:
    """The base fields a case has values for: every field with steps that is not a property."""
    return [
        f["name_canonical"]
        for f in manifest.get("fields") or []
        if f.get("category") != "property" and (f.get("steps") or [])
    ]


@dataclass
class CaseArrays:
    """A materialised case: one superposed (and re-derived) stride per bucket."""

    entry: dict
    fields: list[str]
    arrays: dict[tuple[str, str | None], np.ndarray]


def superpose_case(
    manifest: Mapping,
    fetch_stride: StrideFetcher,
    case: int | Mapping,
    fields: Iterable[str] | None = None,
) -> CaseArrays:
    """Tier A for ``case``: the superposed strides of ``fields`` (default every
    per-case field), keyed ``(field, elem_type or None)``, each shaped like the
    base stride. Raises :class:`NeedsRawRecords` when Tier A cannot do it."""
    entry = find_combination(manifest, case)
    if entry.get("needs_raw"):
        raise NeedsRawRecords(entry.get("raw_reason") or f"case {entry['n']} needs the raw records")
    by_name = {f["name_canonical"]: f for f in manifest.get("fields") or []}
    wanted = list(fields) if fields is not None else case_fields(manifest)
    classes = _field_classes(manifest)
    for name in wanted:
        if name not in by_name:
            raise KeyError(f"no field {name!r} in the manifest")
        if name not in classes:
            raise NeedsRawRecords(f"field {name!r} has no superposition rule")
    terms = entry["terms"]
    coefficients = [float(c) for c, _ in entry["coefficients"]]
    arrays: dict[tuple[str, str | None], np.ndarray] = {}
    for name in _field_order(wanted, classes):
        field = by_name.get(name)
        if field is None:
            raise NeedsRawRecords(f"derivation source field {name!r} is not in the manifest")
        cls = classes.get(name)
        if cls is None:
            raise NeedsRawRecords(f"field {name!r} has no superposition rule")
        steps = [_step_index(field, basic) for basic, _, _ in terms]
        for bucket in _buckets(manifest, field):
            n_values = int(np.prod(bucket.shape))
            strides = [_as_f32(fetch_stride(bucket.blob, i), n_values).reshape(bucket.shape) for i in steps]
            out = superpose(strides, coefficients)
            del strides
            comps = bucket.components
            for comp, rule in cls["derived_components"].items():
                op = DERIVATION_OPS[rule["op"]]["impl"]
                src_name = rule.get("field") or name
                src = out if src_name == name else arrays.get((src_name, bucket.elem_type))
                if src is None:
                    raise NeedsRawRecords(f"{name}: derivation source {src_name!r} has no {bucket.elem_type} bucket")
                src_comps = comps if src_name == name else list(by_name[src_name]["components"])
                if src.shape[:-1] != out.shape[:-1]:
                    raise NeedsRawRecords(f"{name}: derivation source {src_name!r} is laid out differently")
                args = [src[..., src_comps.index(a)] for a in rule["args"]]
                out[..., comps.index(comp)] = op(*args)
            arrays[(name, bucket.elem_type)] = out
    keep = set(wanted)
    return CaseArrays(entry, wanted, {k: v for k, v in arrays.items() if k[0] in keep})


# ---------------------------------------------------------------------------
# Writing a case
# ---------------------------------------------------------------------------


def _step_entry(entry: Mapping) -> dict:
    out = {"i": 0, "value": float(entry["n"]), "label": f"{float(entry['n']):g}"}
    if entry.get("name"):
        out["name"] = entry["name"]
    return out


def _blob_payload(blob_filename: str, header_bytes: int, stride: int) -> dict:
    return {
        "url": blob_filename,
        "header_bytes": header_bytes,
        "stride_bytes": int(stride),
        "dtype": "float32",
        "byte_order": "little",
    }


def _producer(tier: str, engine: str = ENGINE) -> dict:
    try:
        from ada import __version__ as version
    except Exception:  # noqa: BLE001 - a stamp, not a dependency
        version = "unknown"
    return {"engine": engine, "version": str(version), "tier": tier}


def case_overlay(manifest: Mapping, entry: Mapping, field_payloads: list[dict], producer: dict) -> dict:
    """The ``fea.case.json`` overlay for a materialised case."""
    case = {k: entry[k] for k in ("n", "name", "complex", "terms", "coefficients", "recipe_hash") if k in entry}
    return {
        "version": LAZY_CASES_VERSION,
        "kind": "fea_case",
        "bake_version": FEA_BAKE_VERSION,
        "src": manifest.get("src", ""),
        **({"source_sha256": manifest["source_sha256"]} if manifest.get("source_sha256") else {}),
        "case": case,
        "producer": producer,
        "fields": field_payloads,
    }


def _nodal_payload(field: Mapping, entry: Mapping, meta) -> dict:
    from .formats import BLOB_HEADER_BYTES

    scalar_range = {k: list(v) for k, v in meta.scalar_range_per_component.items()}
    if len(field["components"]) >= 2:
        scalar_range["magnitude"] = list(meta.scalar_range_magnitude)
    return {
        "name_canonical": field["name_canonical"],
        "components": list(field["components"]),
        "n_steps": 1,
        "steps": [_step_entry(entry)],
        "blob": _blob_payload(meta.blob_filename, BLOB_HEADER_BYTES, meta.stride_bytes),
        "scalar_range": scalar_range,
    }


def _element_payload(field: Mapping, entry: Mapping, metas: list) -> dict:
    from .formats import ELEM_FIELD_HEADER_BYTES

    # The manifest's roll-up across per_type buckets (build_manifest), per step.
    roll_comp: dict[str, tuple[float, float]] = {}
    roll_mag = (float("inf"), float("-inf"))
    for em in metas:
        for cname, (lo, hi) in em.scalar_range_per_component.items():
            if cname in roll_comp:
                rlo, rhi = roll_comp[cname]
                roll_comp[cname] = (min(rlo, lo), max(rhi, hi))
            else:
                roll_comp[cname] = (lo, hi)
        mlo, mhi = em.scalar_range_magnitude
        roll_mag = (min(roll_mag[0], mlo), max(roll_mag[1], mhi))
    if not (roll_mag[0] != float("inf") and roll_mag[1] != float("-inf")):
        roll_mag = (0.0, 0.0)
    scalar_range = {k: list(v) for k, v in roll_comp.items()}
    if len(field["components"]) >= 2:
        scalar_range["magnitude"] = list(roll_mag)
    return {
        "name_canonical": field["name_canonical"],
        "components": list(field["components"]),
        "n_steps": 1,
        "steps": [_step_entry(entry)],
        "scalar_range": scalar_range,
        "per_type": [
            {
                "elem_type": em.spec.elem_type,
                "n_elements": em.spec.n_elements,
                "n_ips": em.spec.n_ips,
                "blob": _blob_payload(em.blob_filename, ELEM_FIELD_HEADER_BYTES, em.stride_bytes),
                "scalar_range": {k: list(v) for k, v in em.scalar_range_per_component.items()},
            }
            for em in metas
        ],
    }


def write_case(manifest: Mapping, case: CaseArrays, out_dir: os.PathLike, *, producer: dict | None = None) -> dict:
    """Write ``case``'s single-step blobs and its overlay into ``out_dir``.

    The blobs are written by the bake's own writers (same header, same ranges)
    and the overlay last, so a reader that sees ``fea.case.json`` sees a complete
    case. Returns the overlay.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_name = {f["name_canonical"]: f for f in manifest.get("fields") or []}
    entry = case.entry
    value = float(entry["n"])
    payloads: list[dict] = []
    for name in case.fields:
        field = by_name[name]
        comps = list(field["components"])
        buckets = _buckets(manifest, field)
        if bucket_is_nodal(buckets):
            b = buckets[0]
            spec = FieldSpec(
                name=name,
                components=comps,
                n_steps=1,
                n_points=b.shape[0],
                support="nodal",
                step_values=[value],
            )
            writer = FieldBlobWriter(spec, out_dir / b.blob["url"])
            try:
                writer.add(StepValues(0, value, case.arrays[(name, None)]))
            finally:
                writer.close()
            payloads.append(_nodal_payload(field, entry, writer.finish()))
            continue
        metas = []
        for b in buckets:
            spec = ElementFieldSpec(
                name=name,
                components=comps,
                n_steps=1,
                elem_type=b.elem_type,
                n_elements=b.shape[0],
                n_ips=b.shape[1],
                element_labels=[],
                step_values=[value],
            )
            writer = ElementFieldBlobWriter(spec, out_dir / b.blob["url"])
            try:
                writer.add(ElementStepValues(0, value, case.arrays[(name, b.elem_type)]))
            finally:
                writer.close()
            metas.append(writer.finish())
        payloads.append(_element_payload(field, entry, metas))
    overlay = case_overlay(manifest, entry, payloads, producer or _producer("A"))
    write_overlay(overlay, out_dir / CASE_OVERLAY_NAME)
    return overlay


def bucket_is_nodal(buckets: Sequence[_Bucket]) -> bool:
    return len(buckets) == 1 and buckets[0].elem_type is None


def write_overlay(overlay: Mapping, path: os.PathLike) -> None:
    from .manifest import MANIFEST_JSON_SEPARATORS

    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(overlay, f, separators=MANIFEST_JSON_SEPARATORS)


def materialise_case(
    manifest: Mapping,
    fetch_stride: StrideFetcher,
    case: int | Mapping,
    fields: Iterable[str] | None = None,
    *,
    out_dir: os.PathLike | None = None,
) -> CaseArrays | dict:
    """Tier A materialisation of ``case`` (a combination number, or its entry).

    Without ``out_dir``: the superposed strides (:class:`CaseArrays`). With it:
    the case written there (blobs + ``fea.case.json``) and the overlay returned.
    Raises :class:`NeedsRawRecords` when the recipe needs the raw records (see
    :func:`materialise_case_from_source`).
    """
    arrays = superpose_case(manifest, fetch_stride, case, fields)
    if out_dir is None:
        return arrays
    return write_case(manifest, arrays, out_dir)


def local_stride_fetcher(base_dir: os.PathLike) -> StrideFetcher:
    """A :data:`StrideFetcher` over a base bake on local disk."""
    base_dir = pathlib.Path(base_dir)

    def fetch(blob: Mapping, step_index: int) -> bytes:
        stride = int(blob["stride_bytes"])
        with open(base_dir / blob["url"], "rb") as f:
            f.seek(int(blob["header_bytes"]) + int(step_index) * stride)
            data = f.read(stride)
        if len(data) != stride:
            raise ValueError(f"{blob['url']}: short read for step {step_index}")
        return data

    return fetch


# ---------------------------------------------------------------------------
# Raw records (Tier B)
# ---------------------------------------------------------------------------


def materialise_case_from_source(
    src_path: os.PathLike,
    manifest: Mapping,
    case: int | Mapping,
    out_dir: os.PathLike,
    *,
    work_dir: os.PathLike | None = None,
) -> dict:
    """Materialise ``case`` from the source file: a bake of that one case.

    The bake reads the combination's basic cases' raw records and superposes
    them before deriving anything -- the eager path, exactly. Its single-step
    field blobs ARE the case's blobs (same names, same header); only the
    per-case fields are kept, and the overlay is written last.
    """
    import shutil
    import tempfile

    from .bake import bake_fea_artefacts_from_source

    entry = find_combination(manifest, case)
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="fea-case-raw-", dir=work_dir))
    try:
        bake = bake_fea_artefacts_from_source(
            src_path,
            tmp,
            src_key=str(manifest.get("src", "")),
            include_beam_solids=False,
            steps=[int(entry["n"])],
            lazy_combinations=False,
        )
        single = json.loads(bake.manifest_path.read_text(encoding="utf-8"))
        wanted = set(case_fields(manifest))
        payloads = []
        for field in single.get("fields") or []:
            name = field["name_canonical"]
            if name not in wanted or field.get("category") == "property":
                continue
            payload = {
                "name_canonical": name,
                "components": list(field["components"]),
                "n_steps": 1,
                "steps": [_step_entry(entry)],
                "scalar_range": field["scalar_range"],
            }
            if field.get("per_type"):
                payload["per_type"] = [
                    {
                        "elem_type": pt["elem_type"],
                        "n_elements": pt["n_elements"],
                        "n_ips": pt["n_ips"],
                        "blob": pt["blob"],
                        "scalar_range": pt["scalar_range"],
                    }
                    for pt in field["per_type"]
                ]
                files = [pt["blob"]["url"] for pt in field["per_type"]]
            else:
                payload["blob"] = field["blob"]
                files = [field["blob"]["url"]]
            for name_ in files:
                shutil.move(str(tmp / name_), str(out_dir / name_))
            payloads.append(payload)
        overlay = case_overlay(manifest, entry, payloads, _producer("raw"))
        write_overlay(overlay, out_dir / CASE_OVERLAY_NAME)
        return overlay
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


@dataclass
class Envelope:
    """Max / min over cases, per bucket, with the governing case of each.

    ``governing[(field, etype)]`` is uint16 shaped ``(2, *stride)``: index into
    ``cases`` of the case that set the max (row 0) and the min (row 1). Ties go
    to the earlier case; NaN never governs (an entity NaN in every case stays NaN
    with governing index 0).
    """

    field: str
    components: list[str]
    cases: list[int]
    maximum: dict[tuple[str, str | None], np.ndarray]
    minimum: dict[tuple[str, str | None], np.ndarray]
    governing: dict[tuple[str, str | None], np.ndarray]

    def scalar_range(self) -> dict[str, list[float]]:
        """Per-component ``[lo, hi]`` over every bucket (NaN-safe)."""
        out: dict[str, list[float]] = {}
        for key, hi in self.maximum.items():
            lo = self.minimum[key]
            for c, name in enumerate(self.components):
                h = hi[..., c][np.isfinite(hi[..., c])]
                low = lo[..., c][np.isfinite(lo[..., c])]
                if not h.size:
                    continue
                rng = out.get(name)
                pair = [float(low.min()), float(h.max())]
                out[name] = pair if rng is None else [min(rng[0], pair[0]), max(rng[1], pair[1])]
        return out


def envelope(
    manifest: Mapping,
    fetch_stride: StrideFetcher,
    field: str,
    cases: Iterable[int] | None = None,
) -> Envelope:
    """Envelope of ``field`` over the combination cases (default: every Tier-A case).

    Each case is materialised by :func:`superpose_case` (so derived components
    are enveloped after derivation, not superposed). Cases needing the raw
    records are skipped unless named explicitly, which raises."""
    entries = manifest.get("combination_steps") or []
    if cases is None:
        chosen = [int(e["n"]) for e in entries if not e.get("needs_raw")]
    else:
        chosen = [int(c) for c in cases]
    if len(chosen) > np.iinfo(np.uint16).max:
        raise ValueError("too many cases for a uint16 governing index")
    maximum: dict = {}
    minimum: dict = {}
    governing: dict = {}
    for k, n in enumerate(chosen):
        arrays = superpose_case(manifest, fetch_stride, n, [field]).arrays
        for key, arr in arrays.items():
            if key not in maximum:
                maximum[key] = arr.copy()
                minimum[key] = arr.copy()
                governing[key] = np.zeros((2,) + arr.shape, dtype=np.uint16)
                continue
            hi, lo, gov = maximum[key], minimum[key], governing[key]
            up = (arr > hi) | (np.isnan(hi) & ~np.isnan(arr))
            down = (arr < lo) | (np.isnan(lo) & ~np.isnan(arr))
            hi[up] = arr[up]
            lo[down] = arr[down]
            gov[0][up] = k
            gov[1][down] = k
    by_name = {f["name_canonical"]: f for f in manifest.get("fields") or []}
    components = list(by_name[field]["components"]) if field in by_name else []
    return Envelope(field, components, chosen, maximum, minimum, governing)


ENVELOPE_NAME = "fea.envelope.json"
GOVERNING_MAGIC = b"AFGV"


def write_envelope(
    manifest: Mapping,
    fetch_stride: StrideFetcher,
    field: str,
    out_dir: os.PathLike,
    cases: Iterable[int] | None = None,
) -> dict:
    """:func:`envelope` of ``field`` written to ``out_dir``; returns the JSON written last.

    Per blob of the field: a two-step blob in the base format and name (step 0
    the max, step 1 the min) and ``<name>.governing.bin`` (16-byte header:
    ``AFGV``, uint32 version 1, uint32 n_cases, uint32 0; then uint16
    ``[2, *stride]``, indices into ``cases``). ``fea.envelope.json`` carries the
    per-component range over the cases, which cases it covers and which Tier-A
    could not do (``skipped``: they need the raw records).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = envelope(manifest, fetch_stride, field, cases)
    by_name = {f["name_canonical"]: f for f in manifest.get("fields") or []}
    fmeta = by_name[field]
    blobs = []
    for b in _buckets(manifest, fmeta):
        key = (field, b.elem_type)
        if key not in env.maximum:
            continue
        hi, lo = env.maximum[key], env.minimum[key]
        if b.elem_type is None:
            spec = FieldSpec(field, list(b.components), 2, b.shape[0], "nodal", [0.0, 1.0])
            writer = FieldBlobWriter(spec, out_dir / b.blob["url"])
            try:
                writer.add(StepValues(0, 0.0, hi))
                writer.add(StepValues(1, 1.0, lo))
            finally:
                writer.close()
        else:
            spec = ElementFieldSpec(field, list(b.components), 2, b.elem_type, b.shape[0], b.shape[1], [], [0.0, 1.0])
            writer = ElementFieldBlobWriter(spec, out_dir / b.blob["url"])
            try:
                writer.add(ElementStepValues(0, 0.0, hi))
                writer.add(ElementStepValues(1, 1.0, lo))
            finally:
                writer.close()
        writer.finish()
        gov_name = b.blob["url"] + ".governing.bin"
        with open(out_dir / gov_name, "wb") as f:
            f.write(GOVERNING_MAGIC + struct.pack("<III", 1, len(env.cases), 0))
            f.write(np.ascontiguousarray(env.governing[key], dtype="<u2").tobytes())
        blobs.append(
            {
                "elem_type": b.elem_type,
                "blob": {**b.blob, "url": b.blob["url"]},
                "steps": ["max", "min"],
                "governing_url": gov_name,
            }
        )
    all_cases = [int(e["n"]) for e in manifest.get("combination_steps") or []]
    doc = {
        "version": LAZY_CASES_VERSION,
        "kind": "fea_envelope",
        "bake_version": FEA_BAKE_VERSION,
        "src": manifest.get("src", ""),
        "field": field,
        "components": env.components,
        "cases": env.cases,
        "skipped": [n for n in all_cases if n not in set(env.cases)],
        "scalar_range": env.scalar_range(),
        "blobs": blobs,
        "producer": _producer("A"),
    }
    write_overlay(doc, out_dir / ENVELOPE_NAME)
    return doc


__all__ = [
    "CASES_PREFIX",
    "ENVELOPE_NAME",
    "write_envelope",
    "CASE_OVERLAY_NAME",
    "DERIVATION_OPS",
    "LAZY_CASES_VERSION",
    "CaseArrays",
    "Envelope",
    "NeedsRawRecords",
    "case_dir_name",
    "case_fields",
    "case_overlay",
    "classify_fields",
    "combination_entry",
    "derivation_ops_table",
    "envelope",
    "local_stride_fetcher",
    "materialise_case",
    "materialise_case_from_source",
    "recipe_hash",
    "superpose",
    "superpose_case",
    "term_coefficients",
    "write_case",
]
