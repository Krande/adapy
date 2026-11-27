// Frontend adapter over the backend-advertised serializer × tessellator
// matrix. The vocabulary, labels, per-serializer tessellator sets and the
// client-vs-server runtime split are ALL declared by the adapy backend and
// ride to the SPA on the conversion matrix (`options[<target>]`, see
// converter.py `_glb_serializer_options`). This module reads that schema —
// it hardcodes NONE of the choices — so the two dependent reconvert dropdowns
// (gallery tools + ConversionRow) and the client/server routing all agree.

import {runtime, ConversionOption} from "@/runtime/config";
import {workerPreferred} from "@/services/compute/workerAccess";

/** The client tessellator token for adacpp's native CAD->GLB embind modules. */
export const NATIVE_TESSELLATOR = "wasm-native";

export interface SerializerSelection {
    serializer?: string;
    tessellator?: string;
}

export interface SerializerSchema {
    serializer: ConversionOption; // enum with `labels` + `runtime`
    tessellator: ConversionOption; // enum with `labels` + `enum_by` (keyed by serializer)
}

/** The serializer + tessellator option schema for a (source ext, target)
 * pair, or null when the target advertises no serializer choice (only →GLB
 * rows do). */
export function serializerSchemaFor(ext: string, target: string): SerializerSchema | null {
    const opts = runtime.conversionOptionsFor(ext, target);
    const serializer = opts.find((o) => o.name === "serializer");
    const tessellator = opts.find((o) => o.name === "tessellator");
    if (!serializer || !tessellator || !serializer.enum || !tessellator.enum) return null;
    return {serializer, tessellator};
}

/** Tessellator tokens valid for a given serializer, from the backend's
 * `enum_by` dependency map (falls back to the flat enum). */
export function tessellatorsFor(schema: SerializerSchema, serializer: string): readonly string[] {
    const bySer = schema.tessellator.enum_by?.[serializer];
    if (bySer && bySer.length) return bySer;
    return schema.tessellator.enum ?? [];
}

/** The serializer an untouched dropdown starts on. The backend's default names
 * a server serializer (it is the worker's matrix), but the in-browser engine is
 * the default engine: unless the user opted into the server worker (and may use
 * it), the first client serializer the row offers wins. */
function defaultSerializer(schema: SerializerSchema): string {
    const serEnum = schema.serializer.enum ?? [];
    if (!workerPreferred()) {
        const client = serEnum.find((s) => schema.serializer.runtime?.[s] === "client");
        if (client) return client;
    }
    return String(schema.serializer.default ?? serEnum[0] ?? "");
}

/** Resolve a (possibly partial) user selection into a fully-populated,
 * schema-valid pick. Falls the serializer back to its default (see
 * defaultSerializer) and the tessellator to the first token allowed for that
 * serializer, so a stale or empty selection still sends coherent tokens. */
export function normalizeSelection(
    schema: SerializerSchema,
    value: SerializerSelection,
): {serializer: string; tessellator: string; isClient: boolean} {
    const serEnum = schema.serializer.enum ?? [];
    let serializer = value.serializer && serEnum.includes(value.serializer)
        ? value.serializer
        : defaultSerializer(schema);
    const allowed = tessellatorsFor(schema, serializer);
    const isClient = schema.serializer.runtime?.[serializer] === "client";
    // An untouched client row starts on adacpp's native (embind, no Pyodide) module when the
    // row offers it: the same reader/writer as the server, so the same scene hierarchy, and no
    // Python runtime to boot. An explicit choice (e.g. "pyodide") is always honoured.
    const untouchedDefault = isClient && allowed.includes(NATIVE_TESSELLATOR) ? NATIVE_TESSELLATOR : allowed[0] ?? "";
    const tessellator = value.tessellator && allowed.includes(value.tessellator)
        ? value.tessellator
        : untouchedDefault;
    return {serializer, tessellator, isClient};
}

/** Convenience: does the current selection route to the in-browser pipeline? */
export function selectionIsClient(ext: string, target: string, value: SerializerSelection): boolean {
    const schema = serializerSchemaFor(ext, target);
    if (!schema) return false;
    return normalizeSelection(schema, value).isClient;
}

/** A bool conversion option (by name) for an (ext, target) row, or null when the backend doesn't
 * advertise it here. Like the dropdowns, the vocabulary is the backend's — this only finds it. */
export function boolOptionFor(ext: string, target: string, name: string): ConversionOption | null {
    const opt = runtime.conversionOptionsFor(ext, target).find((o) => o.name === name);
    return opt && opt.type === "bool" ? opt : null;
}

/** Can the CURRENTLY selected serializer honour this bool option?
 *
 * `supported_by` lists the depends_on values that can (absent = all can). Asking the backend
 * rather than testing the serializer token here is the point: which path produces a capability is
 * a backend fact, and a toggle offered against a path that ignores it reports something that never
 * happened. */
export function boolOptionSupported(
    ext: string,
    target: string,
    name: string,
    value: SerializerSelection,
): boolean {
    const opt = boolOptionFor(ext, target, name);
    if (!opt) return false;
    if (!opt.supported_by) return true;
    const schema = serializerSchemaFor(ext, target);
    if (!schema) return false;
    const dep = opt.depends_on ?? "serializer";
    if (dep !== "serializer") return false;
    return opt.supported_by.includes(normalizeSelection(schema, value).serializer);
}
