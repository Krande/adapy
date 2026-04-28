import React, {useEffect, useState} from "react";
import {ApiError, AuditEntry, AuditFilters, viewerApi} from "@/services/viewerApi";

// Filterable audit log view. Two layouts:
// * sm:↑ desktop — table with sticky header, fits everything in columns.
// * mobile — collapsible filters + card-per-entry, so a 320px viewport
//   stays readable without horizontal scrolling.
//
// Pagination is keyset on the BIGSERIAL id (the server returns
// next_before_id) — that way the table doesn't shift while new audit
// rows are inserted between pages.

const ACTIONS = ["", "upload", "download", "convert", "view"];
const KINDS = ["", "shared", "project", "user"];

const AuditLogTab: React.FC = () => {
    const [filters, setFilters] = useState<AuditFilters>({limit: 100});
    const [entries, setEntries] = useState<AuditEntry[]>([]);
    const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const activeFilterCount = countActive(filters);

    const reload = async (f: AuditFilters) => {
        setLoading(true);
        setError(null);
        try {
            const r = await viewerApi.adminAudit({...f, before_id: undefined});
            setEntries(r.entries);
            setNextBeforeId(r.next_before_id);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    };

    const loadMore = async () => {
        if (nextBeforeId == null) return;
        setLoading(true);
        try {
            const r = await viewerApi.adminAudit({...filters, before_id: nextBeforeId});
            setEntries((prev) => [...prev, ...r.entries]);
            setNextBeforeId(r.next_before_id);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void reload(filters);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const onFilter = (next: Partial<AuditFilters>) => {
        const merged = {...filters, ...next};
        setFilters(merged);
        void reload(merged);
    };

    return (
        <div className="flex flex-col h-full">
            <div className="border-b border-gray-700">
                <div className="flex items-center gap-2 px-3 py-2 sm:hidden">
                    <button
                        className="bg-gray-800 hover:bg-gray-700 px-2 py-1 rounded text-xs"
                        onClick={() => setFiltersOpen((v) => !v)}
                    >
                        Filters{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""} {filtersOpen ? "▲" : "▼"}
                    </button>
                    <button
                        className="ml-auto bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded text-xs"
                        onClick={() => reload(filters)}
                        disabled={loading}
                    >
                        {loading ? "Loading…" : "Refresh"}
                    </button>
                </div>
                <div
                    className={
                        (filtersOpen ? "flex" : "hidden") +
                        " sm:flex flex-wrap gap-2 px-3 sm:px-4 pb-2 sm:py-2 text-xs"
                    }
                >
                    <FilterInput
                        placeholder="user_sub"
                        value={filters.user_sub || ""}
                        onChange={(v) => onFilter({user_sub: v || undefined})}
                    />
                    <FilterSelect
                        options={KINDS}
                        value={filters.scope_kind || ""}
                        onChange={(v) => onFilter({scope_kind: v || undefined})}
                        placeholder="any kind"
                    />
                    <FilterInput
                        placeholder="scope_id"
                        value={filters.scope_id || ""}
                        onChange={(v) => onFilter({scope_id: v || undefined})}
                    />
                    <FilterSelect
                        options={ACTIONS}
                        value={filters.action || ""}
                        onChange={(v) => onFilter({action: v || undefined})}
                        placeholder="any action"
                    />
                    <button
                        className="hidden sm:inline-block ml-auto bg-blue-700 hover:bg-blue-600 px-2 py-1 rounded"
                        onClick={() => reload(filters)}
                        disabled={loading}
                    >
                        Refresh
                    </button>
                </div>
            </div>
            {error && (
                <div className="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <div className="flex-1 overflow-auto">
                {/* Desktop / tablet table.
                    Time/User/Scope/Action/Target/Status are predictable in
                    width, so we fix them and let Key flex to consume the
                    rest of the row. Removes the postage-stamp truncation
                    that kicked in even when the modal had room to show
                    the full path. */}
                <table className="hidden sm:table w-full text-sm table-fixed min-w-[1200px]">
                    <colgroup>
                        <col className="w-[10rem]"/>
                        <col className="w-[8rem]"/>
                        <col className="w-[12rem]"/>
                        <col className="w-[6rem]"/>
                        <col className="min-w-[14rem]"/>
                        <col className="w-[6rem]"/>
                        <col className="w-[6rem]"/>
                    </colgroup>
                    <thead className="sticky top-0 bg-gray-800">
                    <tr className="text-left">
                        <Th>Time</Th>
                        <Th>User</Th>
                        <Th>Scope</Th>
                        <Th>Action</Th>
                        <Th>Key</Th>
                        <Th>Target</Th>
                        <Th>Status</Th>
                    </tr>
                    </thead>
                    <tbody>
                    {entries.map((e) => (
                        <tr key={e.id} className="border-t border-gray-800 hover:bg-gray-800/40">
                            <Td title={e.ts || ""}>{formatTs(e.ts)}</Td>
                            <Td title={e.user_sub || ""}>{shortSub(e.user_sub)}</Td>
                            <Td title={e.scope_id || ""}>
                                {e.scope_kind}
                                {e.scope_id ? `:${shortSub(e.scope_id)}` : ""}
                            </Td>
                            <Td>{e.action}</Td>
                            <Td title={e.key || ""}>{e.key || ""}</Td>
                            <Td>{e.target_format || ""}</Td>
                            <Td title={e.error || ""}>
                                <span className={statusClass(e.status)}>{e.status || ""}</span>
                            </Td>
                        </tr>
                    ))}
                    </tbody>
                </table>
                {/* Mobile cards */}
                <ul className="sm:hidden divide-y divide-gray-800">
                    {entries.map((e) => (
                        <li key={e.id} className="px-3 py-2 text-xs">
                            <div className="flex items-baseline justify-between gap-2">
                                <span className="font-medium">{e.action}</span>
                                <span className={statusClass(e.status) + " text-[11px]"}>
                                    {e.status || ""}
                                </span>
                            </div>
                            <div className="text-gray-400 mt-0.5">
                                {formatTs(e.ts)} · {e.scope_kind}
                                {e.scope_id ? `:${shortSub(e.scope_id)}` : ""}
                            </div>
                            {e.key && (
                                <div className="text-gray-300 mt-1 break-all" title={e.key}>
                                    {e.key}
                                    {e.target_format ? (
                                        <span className="text-gray-400"> → {e.target_format}</span>
                                    ) : null}
                                </div>
                            )}
                            {e.user_sub && (
                                <div className="text-gray-500 mt-0.5" title={e.user_sub}>
                                    by {shortSub(e.user_sub)}
                                </div>
                            )}
                            {e.error && (
                                <div className="text-red-300 mt-1 break-all" title={e.error}>
                                    {e.error}
                                </div>
                            )}
                        </li>
                    ))}
                </ul>
                {!loading && entries.length === 0 && (
                    <div className="px-4 py-8 text-center text-gray-500 text-sm">
                        No matching audit entries.
                    </div>
                )}
            </div>
            <div className="border-t border-gray-700 px-3 sm:px-4 py-2 flex items-center gap-3 text-xs">
                <span className="text-gray-400">{entries.length} rows</span>
                <button
                    className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded disabled:opacity-50"
                    onClick={loadMore}
                    disabled={loading || nextBeforeId == null}
                >
                    Load more
                </button>
                {loading && <span className="text-gray-500">loading…</span>}
            </div>
        </div>
    );
};

const FilterInput: React.FC<{
    placeholder: string;
    value: string;
    onChange: (v: string) => void;
}> = ({placeholder, value, onChange}) => {
    const [local, setLocal] = useState(value);
    useEffect(() => setLocal(value), [value]);
    return (
        <input
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-full sm:w-56 lg:w-72 text-white"
            placeholder={placeholder}
            value={local}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => onChange(local.trim())}
            onKeyDown={(e) => {
                if (e.key === "Enter") onChange(local.trim());
            }}
        />
    );
};

const FilterSelect: React.FC<{
    options: string[];
    value: string;
    onChange: (v: string) => void;
    placeholder: string;
}> = ({options, value, onChange, placeholder}) => (
    <select
        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white w-full sm:w-auto"
        value={value}
        onChange={(e) => onChange(e.target.value)}
    >
        {options.map((o) =>
            o === "" ? (
                <option key="" value="">
                    {placeholder}
                </option>
            ) : (
                <option key={o} value={o}>
                    {o}
                </option>
            ),
        )}
    </select>
);

const Th: React.FC<{children: React.ReactNode}> = ({children}) => (
    <th className="px-3 py-2 font-medium text-gray-300 whitespace-nowrap">{children}</th>
);

// Truncation lives at the cell level so long values (paths, error
// messages, full subs) don't break layout — but we let the column
// widths do the gating now via <colgroup>, not a hard 20ch cap.
const Td: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
    <td className="px-3 py-1 truncate" title={title}>
        {children}
    </td>
);

function shortSub(s: string | null): string {
    if (!s) return "";
    if (s.length <= 12) return s;
    return `${s.slice(0, 8)}…${s.slice(-4)}`;
}

function formatTs(ts: string | null): string {
    if (!ts) return "";
    return ts.replace("T", " ").slice(0, 19);
}

function statusClass(s: string | null): string {
    if (s === "ok" || s === "done") return "text-green-400";
    if (s === "error") return "text-red-400";
    if (s === "queued") return "text-yellow-300";
    return "text-gray-300";
}

function countActive(f: AuditFilters): number {
    let n = 0;
    if (f.user_sub) n++;
    if (f.scope_kind) n++;
    if (f.scope_id) n++;
    if (f.action) n++;
    return n;
}

export default AuditLogTab;
