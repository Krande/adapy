// A start form from a provider's parameter schema, as data.
//
// A simulation provider describes its parameters with a JSON Schema (an object schema). When no
// plugin supplies a form for the provider, the panel builds one from the schema: one plain input
// per property. This module is the part of that with no React in it -- which fields there are,
// what they start as, and how what was typed becomes the value sent -- so it runs under
// `node --test`.

import type { ParameterSchema } from "@/services/api/simulations";

/** How a property is edited. `json` is the fallback for an object, an array or an untyped
 *  property: a text box holding JSON. */
export type SchemaFieldKind = "string" | "number" | "integer" | "boolean" | "enum" | "json";

export interface SchemaField {
  name: string;
  /** The schema's `title`, else the property name. */
  label: string;
  description?: string;
  kind: SchemaFieldKind;
  required: boolean;
  /** The choices of an `enum` field, as the schema lists them. */
  options?: readonly unknown[];
  minimum?: number;
  maximum?: number;
  default?: unknown;
}

function typeOf(schema: ParameterSchema): string | undefined {
  const t = schema.type;
  if (typeof t === "string") return t;
  // `["number", "null"]`: the first type that is not null is the one to edit.
  if (Array.isArray(t)) return t.find((x) => x !== "null");
  return undefined;
}

function kindOf(schema: ParameterSchema): SchemaFieldKind {
  if (Array.isArray(schema.enum) && schema.enum.length > 0) return "enum";
  const t = typeOf(schema);
  if (t === "string" || t === "number" || t === "integer" || t === "boolean") return t;
  return "json";
}

/** The form's fields, in the schema's property order. Empty for a schema without properties. */
export function schemaFields(schema: ParameterSchema | null | undefined): SchemaField[] {
  const properties = schema?.properties ?? {};
  const required = new Set(schema?.required ?? []);
  return Object.entries(properties).map(([name, sub]) => {
    const s = sub ?? {};
    return {
      name,
      label: typeof s.title === "string" && s.title ? s.title : name,
      description: typeof s.description === "string" ? s.description : undefined,
      kind: kindOf(s),
      required: required.has(name),
      options: Array.isArray(s.enum) ? s.enum : undefined,
      minimum: typeof s.minimum === "number" ? s.minimum : undefined,
      maximum: typeof s.maximum === "number" ? s.maximum : undefined,
      default: s.default,
    };
  });
}

/** What the form starts as: each property's `default`, where the schema gives one. */
export function defaultParameters(schema: ParameterSchema | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of schemaFields(schema)) if (f.default !== undefined) out[f.name] = f.default;
  return out;
}

/** What an input shows for a value (the inverse of `parseFieldInput`). */
export function fieldInputValue(field: SchemaField, value: unknown): string {
  // An enum's input holds the index of the chosen option; -1 is "none chosen".
  if (field.kind === "enum") return String(field.options?.findIndex((o) => o === value) ?? -1);
  if (value === undefined || value === null) return "";
  if (field.kind === "json") return typeof value === "string" ? value : JSON.stringify(value);
  return String(value);
}

/**
 * The value of a field from what its input holds: a number for a numeric field, the chosen
 * option for an enum (the input holds its index), parsed JSON for a `json` field. `undefined`
 * means "left empty" and the property is not sent. Text that is not a number, or not JSON, is
 * kept as the text, so the server's check names the problem instead of the form dropping it.
 */
export function parseFieldInput(field: SchemaField, raw: string | boolean): unknown {
  if (field.kind === "boolean") return typeof raw === "boolean" ? raw : raw === "true";
  const text = String(raw);
  if (text.trim() === "") return undefined;
  if (field.kind === "number" || field.kind === "integer") {
    const n = Number(text);
    return Number.isFinite(n) ? n : text;
  }
  if (field.kind === "enum") {
    const i = Number(text);
    return Number.isInteger(i) && i >= 0 ? field.options?.[i] : undefined;
  }
  if (field.kind === "json") {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return text;
}

/** `values` with `name` set, or removed when the new value is `undefined`. */
export function withParameter(
  values: Readonly<Record<string, unknown>>,
  name: string,
  value: unknown,
): Record<string, unknown> {
  const next = { ...values };
  if (value === undefined) delete next[name];
  else next[name] = value;
  return next;
}

/** The required fields the form has no value for, by label: what to fill in before starting. */
export function missingRequired(schema: ParameterSchema | null | undefined, values: Readonly<Record<string, unknown>>): string[] {
  return schemaFields(schema)
    .filter((f) => f.required && (values[f.name] === undefined || values[f.name] === ""))
    .map((f) => f.label);
}
