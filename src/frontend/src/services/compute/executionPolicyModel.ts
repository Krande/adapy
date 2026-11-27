// The execution policy's vocabulary and pure rules: which engine runs each
// operation, in what order. Mirrors the server's ada.comms.rest.execution_policy
// (DEFAULT_POLICY, validate_policy, complete_policy, merge_policy, lookup) so the
// admin editor validates what the server will accept and the resolver looks up
// exactly what the server enforces.
//
// Free of stores and React: the resolver (executionPolicy.ts), the admin editor
// and the tests all build on it.

export const EXEC_OPERATIONS = [
    "convert",
    "read",
    "fea_bake",
    "procedural_compile",
    "plugin_job",
    "procedural_io",
    "asset_build",
] as const;
export type ExecOperation = (typeof EXEC_OPERATIONS)[number];

export const EXEC_ENGINES = ["native", "pyodide", "worker"] as const;
export type ExecEngine = (typeof EXEC_ENGINES)[number];

/** operation -> qualifier -> ordered fallback list of engines. */
export type ExecutionPolicyDoc = Partial<Record<ExecOperation, Record<string, ExecEngine[]>>>;

export const WILDCARD = "*";

/** Operations whose qualifier is the lower-case source extension incl. the dot. */
export const EXTENSION_OPERATIONS: ReadonlySet<ExecOperation> = new Set(["convert", "read", "fea_bake"]);

/** Operations whose qualifier is a procedural engine slug. */
export const ENGINE_OPERATIONS: ReadonlySet<ExecOperation> = new Set(["procedural_compile", "procedural_io"]);

/** Operations whose qualifier is an asset build capability (a node's `build` claim). */
export const CAPABILITY_OPERATIONS: ReadonlySet<ExecOperation> = new Set(["asset_build"]);

export const OPERATION_LABELS: Record<ExecOperation, string> = {
    convert: "Convert (converter page, re-convert)",
    read: "Open for viewing",
    fea_bake: "FEA result bake",
    procedural_compile: "Procedural compile",
    plugin_job: "Plugin job",
    procedural_io: "Procedural Excel, relocations and IFC/Genie export",
    asset_build: "Asset node build (Sources tab load)",
};

export const ENGINE_LABELS: Record<ExecEngine, string> = {
    native: "Native wasm",
    pyodide: "Pyodide",
    worker: "Server worker",
};

/** What a qualifier names for each operation (the admin editor's placeholder). */
export const QUALIFIER_HINTS: Record<ExecOperation, string> = {
    convert: ".ext",
    read: ".ext",
    fea_bake: ".ext",
    procedural_compile: "engine slug",
    plugin_job: "plugin id",
    procedural_io: "engine slug",
    asset_build: "build capability",
};

const BROWSER_FIRST: ExecEngine[] = ["pyodide", "worker"];
const NATIVE_FIRST: ExecEngine[] = ["native", "pyodide", "worker"];

/** Today's routing: the browser first (adacpp's native module first for STEP/IFC),
 * the worker pool after it. Same as the server's DEFAULT_POLICY. */
export const DEFAULT_EXECUTION_POLICY: Readonly<Record<ExecOperation, Readonly<Record<string, readonly ExecEngine[]>>>> = {
    convert: {"*": BROWSER_FIRST, ".ifc": NATIVE_FIRST, ".step": NATIVE_FIRST, ".stp": NATIVE_FIRST},
    read: {"*": BROWSER_FIRST, ".ifc": NATIVE_FIRST, ".step": NATIVE_FIRST, ".stp": NATIVE_FIRST},
    fea_bake: {"*": BROWSER_FIRST},
    procedural_compile: {"*": BROWSER_FIRST},
    plugin_job: {"*": BROWSER_FIRST},
    procedural_io: {"*": BROWSER_FIRST},
    asset_build: {"*": BROWSER_FIRST},
};

/** A deep, mutable copy of the defaults. */
export function defaultExecutionPolicy(): Record<ExecOperation, Record<string, ExecEngine[]>> {
    const out = {} as Record<ExecOperation, Record<string, ExecEngine[]>>;
    for (const op of EXEC_OPERATIONS) {
        out[op] = {};
        for (const [q, engines] of Object.entries(DEFAULT_EXECUTION_POLICY[op])) out[op][q] = [...engines];
    }
    return out;
}

const EXT_QUALIFIER = /^\.[a-z0-9][a-z0-9_+-]{0,31}$/;
const NAME_QUALIFIER = /^[a-z0-9][a-z0-9_.:@/+-]{0,127}$/;

/** Lower-cased qualifier, or an error message when it is not valid for ``op``. */
export function normaliseQualifier(op: ExecOperation, qualifier: string): {value: string} | {error: string} {
    const q = qualifier.trim().toLowerCase();
    if (q === WILDCARD) return {value: q};
    if (EXTENSION_OPERATIONS.has(op)) {
        return EXT_QUALIFIER.test(q)
            ? {value: q}
            : {error: `${op}: qualifier '${qualifier}' must be '*' or a source extension with its dot (e.g. '.ifc')`};
    }
    if (NAME_QUALIFIER.test(q)) return {value: q};
    const what = ENGINE_OPERATIONS.has(op)
        ? "an engine slug"
        : CAPABILITY_OPERATIONS.has(op)
          ? "a build capability"
          : "a plugin id";
    return {error: `${op}: qualifier '${qualifier}' must be '*' or ${what}`};
}

/** The errors the server would answer a PUT of ``doc`` with (empty = valid). */
export function validationErrors(doc: ExecutionPolicyDoc): string[] {
    const errors: string[] = [];
    for (const [op, rows] of Object.entries(doc)) {
        if (!(EXEC_OPERATIONS as readonly string[]).includes(op)) {
            errors.push(`unknown operation '${op}'`);
            continue;
        }
        const seen = new Set<string>();
        for (const [qualifier, engines] of Object.entries(rows ?? {})) {
            const q = normaliseQualifier(op as ExecOperation, qualifier);
            if ("error" in q) {
                errors.push(q.error);
                continue;
            }
            if (seen.has(q.value)) errors.push(`${op}: qualifier '${q.value}' appears more than once`);
            seen.add(q.value);
            if (!Array.isArray(engines) || engines.length === 0) {
                errors.push(`${op} ${q.value}: needs at least one engine`);
                continue;
            }
            const used = new Set<string>();
            for (const e of engines) {
                if (!(EXEC_ENGINES as readonly string[]).includes(e)) errors.push(`${op} ${q.value}: unknown engine '${e}'`);
                if (used.has(e)) errors.push(`${op} ${q.value}: engine '${e}' is listed twice`);
                used.add(e);
            }
        }
    }
    return errors;
}

/** ``base`` with every (operation, qualifier) row of ``override`` replacing its own. */
export function mergePolicy(base: ExecutionPolicyDoc, override?: ExecutionPolicyDoc | null): ExecutionPolicyDoc {
    const out: ExecutionPolicyDoc = {};
    for (const [op, rows] of Object.entries(base) as [ExecOperation, Record<string, ExecEngine[]>][]) {
        out[op] = Object.fromEntries(Object.entries(rows).map(([q, e]) => [q, [...e]]));
    }
    for (const [op, rows] of Object.entries(override ?? {}) as [ExecOperation, Record<string, ExecEngine[]>][]) {
        const table = (out[op] ??= {});
        for (const [q, e] of Object.entries(rows)) table[q] = [...e];
    }
    return out;
}

/** A global document made whole: an operation it does not name keeps its default
 * table; an operation without a "*" row gets the default one. */
export function completePolicy(doc?: ExecutionPolicyDoc | null): Record<ExecOperation, Record<string, ExecEngine[]>> {
    const out = defaultExecutionPolicy();
    for (const [op, rows] of Object.entries(doc ?? {}) as [ExecOperation, Record<string, ExecEngine[]>][]) {
        if (!out[op]) continue;
        const table = Object.fromEntries(Object.entries(rows).map(([q, e]) => [q, [...e]]));
        if (!table[WILDCARD]) table[WILDCARD] = [...DEFAULT_EXECUTION_POLICY[op][WILDCARD]];
        out[op] = table;
    }
    return out;
}

/** The engine order for (op, qualifier), else (op, "*"). */
export function lookupOrder(policy: ExecutionPolicyDoc, op: ExecOperation, qualifier?: string | null): ExecEngine[] {
    const rows = policy[op] ?? {};
    const q = (qualifier ?? WILDCARD).trim().toLowerCase() || WILDCARD;
    return [...(rows[q] ?? rows[WILDCARD] ?? [])];
}

/** ``order`` with ``preferred`` moved to the front, when it is in ``order``. A user
 * reorders only among the engines the policy allows them. */
export function applyPreference(order: readonly ExecEngine[], preferred?: ExecEngine | null): ExecEngine[] {
    if (!preferred || !order.includes(preferred)) return [...order];
    return [preferred, ...order.filter((e) => e !== preferred)];
}

/** The qualifier a source key gives the extension-qualified operations. */
export function sourceQualifier(sourceKey: string): string {
    const base = sourceKey.slice(sourceKey.lastIndexOf("/") + 1);
    const dot = base.lastIndexOf(".");
    return dot > 0 || (dot === 0 && base.length > 1) ? base.slice(dot).toLowerCase() : WILDCARD;
}
