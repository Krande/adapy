/**
 * Pure detailing-option reconciliation for the cellbuilder — no zustand, no
 * three.js, node-testable. Kept out of cellBuilderStore.ts (like blueprints.ts)
 * so tests don't pull the store's whole dependency graph.
 *
 * The Detailing tab is DATA-DRIVEN: every control comes from the selected
 * engine's advertised `joint_types` specs. This module reconciles the user's
 * current per-joint option state against those specs whenever the engine (or its
 * advertised specs) change — mirroring how `resolveSelectedBlueprint` reconciles
 * the blueprint selection — so stale joints are dropped, newly-advertised ones
 * appear at their defaults, and the user's still-valid edits are preserved.
 */

import type {
  DetailingEngineSummary,
  DetailingFieldSpec,
  DetailingJointTypeSpec,
  DetailingOptionsPayload,
} from "@/services/viewerApi";

export type DetailingFieldValue = number | boolean | string;

/** Per-joint option state: the toggle plus the current value of every field. */
export interface DetailingJointOption {
  enabled: boolean;
  fields: Record<string, DetailingFieldValue>;
}

/** Keyed by joint-type slug. */
export type DetailingOptions = Record<string, DetailingJointOption>;

/** The field's default coerced to the declared type (defensive against a spec
 * that advertises a mismatched default). */
export function fieldDefault(spec: DetailingFieldSpec): DetailingFieldValue {
  if (spec.type === "bool") return Boolean(spec.default);
  if (spec.type === "number") {
    const n = Number(spec.default);
    return Number.isFinite(n) ? n : 0;
  }
  // enum / string
  return spec.default != null ? String(spec.default) : "";
}

/** True when `value` is a usable value for `spec` (right JS type, and for enums
 * one of the advertised options). Invalid values fall back to the default. */
function isValidValue(spec: DetailingFieldSpec, value: unknown): boolean {
  if (value == null) return false;
  if (spec.type === "bool") return typeof value === "boolean";
  if (spec.type === "number")
    return typeof value === "number" && Number.isFinite(value);
  // enum
  if (typeof value !== "string") return false;
  return spec.options ? spec.options.includes(value) : true;
}

/** Clamp a number field to its advertised [min, max] (no-op when unbounded). */
export function clampField(spec: DetailingFieldSpec, value: number): number {
  let v = value;
  if (typeof spec.min === "number" && v < spec.min) v = spec.min;
  if (typeof spec.max === "number" && v > spec.max) v = spec.max;
  return v;
}

function reconcileJoint(
  spec: DetailingJointTypeSpec,
  prev: DetailingJointOption | undefined,
): DetailingJointOption {
  const enabled = prev
    ? prev.enabled
    : (spec.default_enabled ?? true);
  const fields: Record<string, DetailingFieldValue> = {};
  for (const f of spec.fields ?? []) {
    const prior = prev?.fields?.[f.name];
    fields[f.name] = isValidValue(f, prior)
      ? (prior as DetailingFieldValue)
      : fieldDefault(f);
  }
  return { enabled, fields };
}

/**
 * Reconcile per-joint options against the SELECTED engine's advertised specs.
 * Keeps still-advertised joints (preserving valid edits, defaulting invalid or
 * missing ones), drops joints the engine no longer advertises, and adds new ones
 * at their defaults. Returns an empty map when there is no engine / it advertises
 * no joint types (e.g. `none`). Pure + deterministic.
 */
export function resolveDetailingOptions(
  engine: DetailingEngineSummary | undefined,
  prev: DetailingOptions,
): DetailingOptions {
  const next: DetailingOptions = {};
  for (const jt of engine?.joint_types ?? []) {
    next[jt.slug] = reconcileJoint(jt, prev[jt.slug]);
  }
  return next;
}

/**
 * Flatten the per-joint state into the wire payload the compile call ships as
 * `detailing_options` — `{slug: {enabled, <field>: value}}`. Number fields are
 * clamped to their advertised range so the server never sees an out-of-band knob.
 * Returns `null` when nothing is advertised (so the caller omits the param and
 * keeps the plain cache key).
 */
export function toDetailingOptionsPayload(
  engine: DetailingEngineSummary | undefined,
  options: DetailingOptions,
): DetailingOptionsPayload | null {
  if (!engine?.joint_types?.length) return null;
  const payload: DetailingOptionsPayload = {};
  for (const jt of engine.joint_types) {
    const opt = options[jt.slug];
    if (!opt) continue;
    const entry: Record<string, DetailingFieldValue> = {
      enabled: opt.enabled,
    };
    for (const f of jt.fields ?? []) {
      let v = opt.fields[f.name];
      if (v === undefined) v = fieldDefault(f);
      if (f.type === "number" && typeof v === "number") v = clampField(f, v);
      entry[f.name] = v;
    }
    payload[jt.slug] = entry;
  }
  return payload;
}
