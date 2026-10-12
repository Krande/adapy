import React, {useEffect, useState} from "react";

import type {DbTableRows} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";

import {cellView, hasNext, hasPrev, nextOffset, pageLabel, prevOffset, ROWS_PAGE_SIZE, type PageInfo} from "./dbRows";

// Admin -> Database, one table's rows, a page at a time. The API decides what may be read (only
// tables the storage report lists), reads them read-only with a short timeout, cuts long values
// and redacts secret-looking columns (db/table_rows.py); this only pages and shows. A cell shows
// its first characters; hover for the value, click to expand it in place.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const btn = "rounded-sm px-2 py-0.5 text-xs disabled:opacity-50 bg-gray-700 hover:bg-gray-600";

const TableRowsView: React.FC<{table: string; onClose: () => void}> = ({table, onClose}) => {
    const [offset, setOffset] = useState(0);
    const [data, setData] = useState<DbTableRows | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);

    useEffect(() => setOffset(0), [table]);
    useEffect(() => {
        let live = true;
        setLoading(true);
        setError(null);
        viewerApi
            .adminDbTableRows(table, offset, ROWS_PAGE_SIZE)
            .then((d) => {
                if (!live) return;
                setData(d);
                setExpanded(null);
            })
            .catch((e) => live && setError(errText(e)))
            .finally(() => live && setLoading(false));
        return () => {
            live = false;
        };
    }, [table, offset]);

    const shown = data && data.table === table ? data : null;
    const info: PageInfo | null = shown
        ? {offset: shown.offset, count: shown.rows.length, limit: shown.limit, total: shown.total, totalExact: shown.total_exact}
        : null;

    return (
        <div className="rounded-sm border border-gray-800 bg-gray-900/40 p-2 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-mono text-gray-200">{table}</span>
                {info && <span className="text-gray-400">{pageLabel(info)}</span>}
                {shown && (
                    <span className="text-gray-500">
                        · by {shown.order_by.join(", ")} · {shown.took_ms} ms
                    </span>
                )}
                <button type="button" className={btn} disabled={loading || !info || !hasPrev(info)} onClick={() => info && setOffset(prevOffset(info))}>
                    Prev
                </button>
                <button type="button" className={btn} disabled={loading || !info || !hasNext(info)} onClick={() => info && setOffset(nextOffset(info))}>
                    Next
                </button>
                {loading && <span className="text-gray-500">loading…</span>}
                <button type="button" className={`${btn} ml-auto`} onClick={onClose}>
                    Close
                </button>
            </div>
            {error && <div className="text-[11px] text-red-300">{error}</div>}
            {shown && (
                <div className="w-full overflow-x-auto">
                    <table className="text-[11px] font-mono">
                        <thead className="text-left text-gray-400">
                            <tr>
                                {shown.columns.map((c) => (
                                    <th key={c.name} className="pr-3 align-bottom font-normal whitespace-nowrap">
                                        <div className="text-gray-200">{c.name}</div>
                                        <div className="text-gray-500">
                                            {c.type}
                                            {c.redacted ? <span className="text-amber-300"> · redacted</span> : null}
                                        </div>
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {shown.rows.map((row, r) => (
                                <tr key={shown.offset + r} className="border-t border-gray-800/60 align-top">
                                    {row.map((value, c) => {
                                        const key = `${r}:${c}`;
                                        const cell = cellView(value);
                                        const open = expanded === key;
                                        const tone = cell.kind === "value" ? "text-gray-200" : cell.kind === "redacted" ? "text-amber-300" : "text-gray-500";
                                        return (
                                            <td
                                                key={c}
                                                className={`pr-3 py-0.5 ${tone} ${cell.clipped ? "cursor-pointer" : ""} ${open ? "whitespace-pre-wrap break-all min-w-[24rem] max-w-[48rem]" : "whitespace-nowrap"}`}
                                                title={cell.clipped && !open ? cell.full : undefined}
                                                onClick={cell.clipped ? () => setExpanded(open ? null : key) : undefined}
                                            >
                                                {open ? cell.full : cell.text}
                                            </td>
                                        );
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

export default TableRowsView;
