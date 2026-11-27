// Editor state for an execution-policy document (the Admin Panel's Execution tab
// and the per-user override in Admin -> Users): a flat list of rows, one per
// (operation, qualifier), each an ordered engine list. React-free so the rules are
// testable without a bundler.

import {
    EXEC_ENGINES,
    EXEC_OPERATIONS,
    lookupOrder,
    normaliseQualifier,
    validationErrors,
    WILDCARD,
    type ExecEngine,
    type ExecOperation,
    type ExecutionPolicyDoc,
} from "@/services/compute/executionPolicyModel";

export interface PolicyRow {
    op: ExecOperation;
    qualifier: string;
    engines: ExecEngine[];
}

function opIndex(op: ExecOperation): number {
    return EXEC_OPERATIONS.indexOf(op);
}

/** Rows in display order: by operation, "*" first, then the qualifiers A-Z. */
export function sortRows(rows: readonly PolicyRow[]): PolicyRow[] {
    return [...rows].sort((a, b) => {
        if (a.op !== b.op) return opIndex(a.op) - opIndex(b.op);
        if (a.qualifier === WILDCARD) return -1;
        if (b.qualifier === WILDCARD) return 1;
        return a.qualifier.localeCompare(b.qualifier);
    });
}

export function rowsFromPolicy(doc: ExecutionPolicyDoc | null | undefined): PolicyRow[] {
    const rows: PolicyRow[] = [];
    for (const op of EXEC_OPERATIONS) {
        for (const [qualifier, engines] of Object.entries(doc?.[op] ?? {})) {
            rows.push({op, qualifier, engines: [...engines]});
        }
    }
    return sortRows(rows);
}

export function policyFromRows(rows: readonly PolicyRow[]): ExecutionPolicyDoc {
    const doc: ExecutionPolicyDoc = {};
    for (const row of rows) {
        (doc[row.op] ??= {})[row.qualifier] = [...row.engines];
    }
    return doc;
}

/** Move the engine at ``index`` one place earlier (delta -1) or later (+1). */
export function moveEngine(engines: readonly ExecEngine[], index: number, delta: -1 | 1): ExecEngine[] {
    const to = index + delta;
    if (index < 0 || index >= engines.length || to < 0 || to >= engines.length) return [...engines];
    const out = [...engines];
    [out[index], out[to]] = [out[to], out[index]];
    return out;
}

/** Add ``engine`` at the end, or remove it when it is listed. */
export function toggleEngine(engines: readonly ExecEngine[], engine: ExecEngine): ExecEngine[] {
    return engines.includes(engine) ? engines.filter((e) => e !== engine) : [...engines, engine];
}

/** Engines not yet in a row's list (what its "add" control offers). */
export function missingEngines(engines: readonly ExecEngine[]): ExecEngine[] {
    return EXEC_ENGINES.filter((e) => !engines.includes(e));
}

export function updateRow(
    rows: readonly PolicyRow[],
    op: ExecOperation,
    qualifier: string,
    engines: ExecEngine[],
): PolicyRow[] {
    return rows.map((r) => (r.op === op && r.qualifier === qualifier ? {...r, engines} : r));
}

/** Add a qualifier row for ``op``, starting from ``initial`` (default: what the
 * rows answer for that qualifier now, i.e. its "*" row). */
export function addRow(
    rows: readonly PolicyRow[],
    op: ExecOperation,
    qualifier: string,
    initial?: readonly ExecEngine[],
): {rows: PolicyRow[]} | {error: string} {
    const q = normaliseQualifier(op, qualifier);
    if ("error" in q) return {error: q.error};
    if (rows.some((r) => r.op === op && r.qualifier === q.value)) {
        return {error: `${op} already has a row for '${q.value}'`};
    }
    const engines = initial ? [...initial] : lookupOrder(policyFromRows(rows), op, q.value);
    return {rows: sortRows([...rows, {op, qualifier: q.value, engines: engines.length ? engines : ["pyodide"]}])};
}

/** Remove a row. The global document keeps its "*" rows (``keepWildcard``): every
 * lookup falls back to one. */
export function removeRow(
    rows: readonly PolicyRow[],
    op: ExecOperation,
    qualifier: string,
    keepWildcard = true,
): PolicyRow[] {
    if (keepWildcard && qualifier === WILDCARD) return [...rows];
    return rows.filter((r) => !(r.op === op && r.qualifier === qualifier));
}

/** What the server would refuse in these rows (empty = can be saved). */
export function rowErrors(rows: readonly PolicyRow[]): string[] {
    return validationErrors(policyFromRows(rows));
}

/** Whether two documents say the same thing (row order aside). */
export function samePolicy(a: ExecutionPolicyDoc, b: ExecutionPolicyDoc): boolean {
    const norm = (d: ExecutionPolicyDoc) => JSON.stringify(rowsFromPolicy(d));
    return norm(a) === norm(b);
}
