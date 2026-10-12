import type {DbCell} from "@/services/api/adminDb";

import {formatRows} from "./dbStorage";

// Admin -> Database, a table's rows: the paging and cell rules, React-free so the node test
// runner can load them (see adminTabs.ts). The API bounds every cell already (db/table_rows.py);
// this only decides what a page says about itself and how much of a cell fits in a column.

export const ROWS_PAGE_SIZE = 50;
/** Characters of a cell shown in its column; the rest is one hover or click away. */
export const CELL_PREVIEW_CHARS = 60;
/** What the API appends to a value it cut (``TRUNCATED_MARK``). */
export const SERVER_TRUNCATED_MARK = "…[truncated]";

export interface PageInfo {
    offset: number;
    /** Rows on this page. */
    count: number;
    limit: number;
    total: number;
    totalExact: boolean;
}

/** "rows 51–100 of ~1.3M", "no rows" on an empty table, "no rows past 1,200" beyond the end. */
export function pageLabel(p: PageInfo): string {
    const of = formatRows(p.total, p.totalExact);
    if (!p.count) return p.offset ? `no rows past ${p.offset.toLocaleString("en-US")}` : "no rows";
    return `rows ${(p.offset + 1).toLocaleString("en-US")}–${(p.offset + p.count).toLocaleString("en-US")} of ${of}`;
}

export function hasPrev(p: PageInfo): boolean {
    return p.offset > 0;
}

/** An exact total says where the end is; an estimate only that a full page may have more. */
export function hasNext(p: PageInfo): boolean {
    if (p.count < p.limit) return false;
    return p.totalExact ? p.offset + p.count < p.total : true;
}

export function prevOffset(p: Pick<PageInfo, "offset" | "limit">): number {
    return Math.max(0, p.offset - p.limit);
}

export function nextOffset(p: Pick<PageInfo, "offset" | "limit">): number {
    return p.offset + p.limit;
}

export interface CellView {
    /** What the column shows. */
    text: string;
    /** The whole value as the API sent it (for the hover title / the expanded cell). */
    full: string;
    /** Shown shorter than sent. */
    clipped: boolean;
    /** The API cut it too: even `full` is not the whole value. */
    serverCut: boolean;
    kind: "null" | "redacted" | "bytes" | "value";
}

export function cellView(value: DbCell, maxChars: number = CELL_PREVIEW_CHARS): CellView {
    if (value === null || value === undefined) {
        return {text: "null", full: "null", clipped: false, serverCut: false, kind: "null"};
    }
    const full = typeof value === "string" ? value : String(value);
    const kind = full === "<redacted>" ? "redacted" : /^<\d+ bytes>$/.test(full) ? "bytes" : "value";
    // One line per cell: a newline would make a row taller than its neighbours.
    const flat = full.replace(/\s*\n\s*/g, " ⏎ ");
    const clipped = flat.length > maxChars || flat !== full;
    return {
        text: flat.length > maxChars ? `${flat.slice(0, maxChars - 1)}…` : flat,
        full,
        clipped,
        serverCut: typeof value === "string" && value.endsWith(SERVER_TRUNCATED_MARK),
        kind,
    };
}
