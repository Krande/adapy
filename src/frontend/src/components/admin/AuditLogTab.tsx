import React, {useEffect, useState} from "react";
import {ApiError, AuditEntry, AuditFilters, viewerApi} from "@/services/viewerApi";

// Filterable audit log view. Client-side filters call /api/admin/audit
// with query-string params; pagination is keyset (server returns
// next_before_id) so the table scrolls cleanly even as new rows are
// inserted while the operator is reading.

const ACTIONS = ["", "upload", "download", "convert", "view"];
const KINDS = ["", "shared", "project", "user"];

const AuditLogTab: React.FC = () => {
    const [filters, setFilters] = useState<AuditFilters>({limit: 100});
    const [entries, setEntries] = useState<AuditEntry[]>([]);
    const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

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
            <div className="flex flex-wrap gap-2 px-4 py-2 border-b border-gray-700 text-xs">
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
                    className="ml-auto bg-blue-700 hover:bg-blue-600 px-2 py-1 rounded"
                    onClick={() => reload(filters)}
                    disabled={loading}
                >
                    Refresh
                </button>
            </div>
            {error && (
                <div className="px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <div className="flex-1 overflow-auto">
                <table className="w-full text-xs">
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
                            <Td title={e.ts || ""}>{e.ts ? e.ts.replace("T", " ").slice(0, 19) : ""}</Td>
                            <Td title={e.user_sub || ""}>{shortSub(e.user_sub)}</Td>
                            <Td>
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
                {!loading && entries.length === 0 && (
                    <div className="px-4 py-8 text-center text-gray-500 text-sm">
                        No matching audit entries.
                    </div>
                )}
            </div>
            <div className="border-t border-gray-700 px-4 py-2 flex items-center gap-3 text-xs">
                <span className="text-gray-400">{entries.length} rows</span>
                <button
                    className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded disabled:opacity-50"
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
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-44 text-white"
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
        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white"
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
    <th className="px-3 py-1 font-medium text-gray-300">{children}</th>
);

const Td: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
    <td className="px-3 py-1 truncate max-w-[20ch]" title={title}>
        {children}
    </td>
);

function shortSub(s: string | null): string {
    if (!s) return "";
    if (s.length <= 12) return s;
    return `${s.slice(0, 8)}…${s.slice(-4)}`;
}

function statusClass(s: string | null): string {
    if (s === "ok" || s === "done") return "text-green-400";
    if (s === "error") return "text-red-400";
    if (s === "queued") return "text-yellow-300";
    return "text-gray-300";
}

export default AuditLogTab;
