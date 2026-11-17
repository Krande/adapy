/**
 * A procedural model's SOURCE WORKBOOK in Storage: the workbook a tool made the model from,
 * filed beside it as `<folder>/<stem>.xlsx` while the model is `<folder>/<stem>`.
 *
 * The two are one thing to the person -- the model is the workbook, read; saving the model
 * writes the workbook -- so the browser shows one row: the model's, which names its source.
 * The workbook file is still in storage (a download, a job's input); it is only not a row
 * of its own. Pure helpers over names; nothing here knows the store or the server.
 */

const WORKBOOK_SUFFIXES = [".xlsx", ".xlsm"] as const;

/** The key of model `name`'s source workbook among `keys`, or null. */
export function sourceWorkbookKey(name: string, keys: Iterable<string>): string | null {
    const want = new Set(WORKBOOK_SUFFIXES.map((s) => (name + s).toLowerCase()));
    for (const k of keys) if (want.has(k.toLowerCase())) return k;
    return null;
}

/** model name -> its source workbook key, for the models that have one. */
export function sourceWorkbooks(modelNames: Iterable<string>, keys: Iterable<string>): Map<string, string> {
    const all = [...keys];
    const out = new Map<string, string>();
    for (const name of modelNames) {
        const key = sourceWorkbookKey(name, all);
        if (key) out.set(name, key);
    }
    return out;
}

/** The file name a source key shows under: its last path part. */
export function sourceLabel(key: string): string {
    const parts = key.split("/");
    return parts[parts.length - 1] || key;
}
