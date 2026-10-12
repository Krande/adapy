import React, {useCallback, useEffect, useMemo, useState} from "react";

import type {
    DbAreaStorage,
    DbAttributePartition,
    DbPruneResult,
    DbPruneSpec,
    DbStorageReport,
} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {formatBytes} from "@/utils/format";

import AdminTabShell from "../AdminTabShell";
import {
    ageRange,
    collectionKey,
    formatRows,
    pruneSpecFor,
    retentionChanges,
    sharePercent,
    type PruneForm,
} from "./dbStorage";
import TableRowsView from "./TableRowsView";

// Admin -> Database: what the database itself holds, by feature area, and pruning what is only
// history. This database is not the system of record for run history, audit rows or the attribute
// index (that is rebuilt from each provider's export), so an admin can drop what is older than
// they care about -- by hand here (Preview, then Confirm), or daily from a retention.
//
// The server decides what is safe: nothing in flight is ever deleted (a running sweep or export,
// a queued change check, a running audit run and its rows) -- those come back as "protected" in the
// preview. Sizes and row counts are the catalogue's (estimates are marked "~"), so this view never
// scans a large table. The rules live in db/storage_insight.py.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const bytes = (n: number | null | undefined) => formatBytes(n, {compact: true});
const fmtTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");

const btn = "rounded-sm px-2 py-0.5 text-xs disabled:opacity-50";
const input = "bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5 text-xs w-16 text-right";

const SizeBar: React.FC<{part: number; whole: number}> = ({part, whole}) => (
    <div className="h-1.5 w-24 bg-gray-800 rounded-sm overflow-hidden" title={`${sharePercent(part, whole)}% of the tables`}>
        <div className="h-full bg-blue-500" style={{width: `${Math.min(100, (part / Math.max(whole, 1)) * 100)}%`}}/>
    </div>
);

const PreviewView: React.FC<{result: DbPruneResult}> = ({result}) => (
    <div className="space-y-1">
        {result.areas.map((a) => (
            <div key={a.id} className="text-[11px] text-gray-300">
                <div>
                    {result.dry_run ? "Would remove" : "Removed"} <b>{a.rows.toLocaleString()}</b> row(s), about{" "}
                    <b>{bytes(a.est_bytes)}</b>
                    {a.cutoff ? ` — older than ${new Date(a.cutoff).toLocaleDateString()}` : ""}
                    {a.protected ? (
                        <span className="text-amber-300"> · {a.protected} in flight, kept</span>
                    ) : null}
                </div>
                <ul className="ml-3 text-gray-400">
                    {a.tables.map((t) => (
                        <li key={`${t.table}-${t.note ?? ""}`}>
                            {t.table}: {t.rows.toLocaleString()} ({bytes(t.est_bytes)}){t.note ? ` — ${t.note}` : ""}
                        </li>
                    ))}
                </ul>
                {!!a.collections?.length && (
                    <ul className="ml-3 text-gray-400">
                        {a.collections.map((c) => (
                            <li key={collectionKey(c)}>
                                {c.provider} / {c.collection} ({c.scope}): {c.rows.toLocaleString()} rows, {bytes(c.est_bytes)}
                                {c.dropped === false ? <span className="text-red-300"> — not dropped</span> : null}
                            </li>
                        ))}
                    </ul>
                )}
                {a.errors?.map((e) => (
                    <div key={e} className="text-red-300">{e}</div>
                ))}
            </div>
        ))}
        <div className="text-[11px] text-gray-500">{result.note}</div>
        {!!result.vacuum.errors.length && (
            <div className="text-[11px] text-amber-300">VACUUM: {result.vacuum.errors.join("; ")}</div>
        )}
    </div>
);

/** One area's prune: Preview (a dry run) first; Confirm deletes exactly what was previewed. */
const PruneForm_: React.FC<{
    area: DbAreaStorage;
    selected: ReadonlySet<string>;
    onDone: () => void;
}> = ({area, selected, onDone}) => {
    const [days, setDays] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [preview, setPreview] = useState<{spec: string; result: DbPruneResult} | null>(null);
    const [done, setDone] = useState<DbPruneResult | null>(null);
    const form: PruneForm = {days, collections: area.prune === "collections" ? selected : new Set()};
    const built = pruneSpecFor(area, form);
    const specText = "spec" in built ? JSON.stringify(built.spec) : null;
    // A preview stands only for the request it was made with.
    const current = preview && preview.spec === specText ? preview.result : null;

    const run = async (dryRun: boolean) => {
        if (!("spec" in built)) return;
        setBusy(true);
        setError(null);
        try {
            const result = await viewerApi.adminDbPrune({[area.id]: built.spec as DbPruneSpec}, dryRun);
            if (dryRun) {
                setPreview({spec: specText as string, result});
                setDone(null);
            } else {
                setPreview(null);
                setDone(result);
                onDone();
            }
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="rounded-sm border border-gray-800 bg-gray-900/40 p-2 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-gray-300">Prune</span>
                {area.prune === "collections" && (
                    <span className="text-gray-400">
                        {selected.size ? `${selected.size} ticked collection(s) below, or` : "tick collections below, or"}
                    </span>
                )}
                <label className="flex items-center gap-1 text-gray-400">
                    {area.prune === "collections" ? "not ingested for" : "older than"}
                    <input
                        className={input}
                        inputMode="numeric"
                        value={days}
                        onChange={(e) => setDays(e.target.value)}
                        disabled={area.prune === "collections" && selected.size > 0}
                        aria-label={`${area.label}: days`}
                    />
                    days
                </label>
                <button
                    type="button"
                    className={`${btn} bg-gray-700 hover:bg-gray-600`}
                    disabled={busy || !("spec" in built)}
                    title={"error" in built ? built.error : "Count what would go; nothing is changed"}
                    onClick={() => void run(true)}
                >
                    Preview
                </button>
                {current && current.rows > 0 && (
                    <button
                        type="button"
                        className={`${btn} bg-red-700 hover:bg-red-600`}
                        disabled={busy}
                        onClick={() => void run(false)}
                    >
                        Confirm: delete {current.rows.toLocaleString()} row(s)
                    </button>
                )}
                {current && current.rows === 0 && <span className="text-gray-500">nothing old enough</span>}
            </div>
            {area.prune_note && <div className="text-[11px] text-gray-500">{area.prune_note}</div>}
            {error && <div className="text-[11px] text-red-300">{error}</div>}
            {current && <PreviewView result={current}/>}
            {done && <PreviewView result={done}/>}
        </div>
    );
};

const PartitionsTable: React.FC<{
    partitions: DbAttributePartition[];
    selected: ReadonlySet<string>;
    onToggle: (key: string) => void;
}> = ({partitions, selected, onToggle}) => (
    <table className="w-full text-[11px]">
        <thead className="text-gray-500 text-left">
            <tr>
                <th className="w-6"/>
                <th>Provider / collection</th>
                <th>Scope</th>
                <th className="text-right">Rows</th>
                <th className="text-right">Size</th>
                <th className="text-right">Indexes</th>
                <th>Ingested</th>
            </tr>
        </thead>
        <tbody>
            {partitions.map((p) => {
                const ref = p.scope && p.provider && p.collection ? {scope: p.scope, provider: p.provider, collection: p.collection} : null;
                const key = ref ? collectionKey(ref) : p.name;
                return (
                    <tr key={p.name} className="border-t border-gray-800">
                        <td>
                            {ref && (
                                <input
                                    type="checkbox"
                                    checked={selected.has(key)}
                                    onChange={() => onToggle(key)}
                                    aria-label={`select ${p.provider}/${p.collection}`}
                                />
                            )}
                        </td>
                        <td className="text-gray-200">
                            {ref ? `${p.provider} / ${p.collection}` : <span className="text-amber-300">{p.name} (no catalogue row)</span>}
                            {p.revision ? <span className="text-gray-500"> · {p.revision}</span> : null}
                        </td>
                        <td className="text-gray-400 break-all">{p.scope ?? "—"}</td>
                        <td className="text-right">{formatRows(p.rows, p.rows_exact)}</td>
                        <td className="text-right">{bytes(p.total_bytes)}</td>
                        <td className="text-right text-gray-400">{bytes(p.index_bytes)}</td>
                        <td className="text-gray-400">{fmtTime(p.ingested_at)}</td>
                    </tr>
                );
            })}
        </tbody>
    </table>
);

const AreaRow: React.FC<{
    area: DbAreaStorage;
    whole: number;
    open: boolean;
    onToggle: () => void;
    retention?: number;
}> = ({area, whole, open, onToggle, retention}) => (
    <tr className="border-t border-gray-800 cursor-pointer hover:bg-gray-800/40" onClick={onToggle}>
        <td className="py-1 pr-2 text-gray-200">
            <span className="text-gray-500 mr-1">{open ? "▾" : "▸"}</span>
            {area.label}
        </td>
        <td className="pr-2"><SizeBar part={area.total_bytes} whole={whole}/></td>
        <td className="pr-2 text-right">{bytes(area.total_bytes)}</td>
        <td className="pr-2 text-right text-gray-400 hidden sm:table-cell">{bytes(area.table_bytes)}</td>
        <td className="pr-2 text-right text-gray-400 hidden sm:table-cell">{bytes(area.index_bytes)}</td>
        <td className="pr-2 text-right text-gray-400 hidden md:table-cell">{bytes(area.toast_bytes)}</td>
        <td className="pr-2 text-right">{formatRows(area.rows, area.tables.every((t) => t.rows_exact))}</td>
        <td className="pr-2 text-gray-400 hidden md:table-cell">{ageRange(area.oldest, area.newest)}</td>
        <td className="text-gray-400">{area.prune ? (retention ? `${retention} d` : "off") : "—"}</td>
    </tr>
);

const RetentionEditor: React.FC<{report: DbStorageReport; onSaved: () => void}> = ({report, onSaved}) => {
    const prunable = report.areas.filter((a) => a.prune);
    const initial = useMemo(
        () =>
            Object.fromEntries(
                report.areas.filter((a) => a.prune).map((a) => [a.id, report.retention[a.id] ? String(report.retention[a.id]) : ""]),
            ),
        [report],
    );
    const [edited, setEdited] = useState<Record<string, string>>(initial);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => setEdited(initial), [initial]);
    const {changes, invalid} = retentionChanges(report.retention, edited);
    const dirty = Object.keys(changes).length > 0;
    const last = report.retention_last_run;

    const save = async () => {
        setBusy(true);
        setError(null);
        try {
            await viewerApi.adminDbSetRetention(changes);
            onSaved();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <section className="space-y-1.5">
            <h3 className="text-xs font-semibold text-gray-300">Automatic retention</h3>
            <p className="text-[11px] text-gray-500">
                Once a day the API prunes each area below that has a retention, by the same rules as a manual prune
                (nothing in flight is deleted). Empty = off.
                {last ? ` Last run ${fmtTime(last.at)}: ${last.result?.rows?.toLocaleString() ?? 0} row(s), ~${bytes(last.result?.est_bytes ?? 0)}.` : " It has not run yet."}
                {last?.result?.errors?.length ? <span className="text-amber-300"> Errors: {last.result.errors.join("; ")}</span> : null}
            </p>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
                {prunable.map((a) => (
                    <label key={a.id} className="flex items-center gap-1 text-[11px] text-gray-400">
                        {a.label}
                        <input
                            className={`${input} ${invalid.includes(a.id) ? "border-red-500" : ""}`}
                            inputMode="numeric"
                            value={edited[a.id] ?? ""}
                            onChange={(e) => setEdited({...edited, [a.id]: e.target.value})}
                            aria-label={`${a.label}: retention days`}
                        />
                        d
                    </label>
                ))}
            </div>
            <div className="flex items-center gap-2">
                <button
                    type="button"
                    className={`${btn} bg-blue-700 hover:bg-blue-600`}
                    disabled={busy || !dirty || invalid.length > 0}
                    onClick={() => void save()}
                >
                    Save retention
                </button>
                {invalid.length > 0 && <span className="text-[11px] text-red-300">whole days, at least 1</span>}
                {error && <span className="text-[11px] text-red-300">{error}</span>}
            </div>
        </section>
    );
};

const DatabaseTab: React.FC = () => {
    const [report, setReport] = useState<DbStorageReport | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
    const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
    /** The table whose rows are open below its line (one at a time). */
    const [rowsOf, setRowsOf] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setReport(await viewerApi.adminDbStorage());
        } catch (e) {
            setError(errText(e));
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => {
        void load();
    }, [load]);

    const toggle = (set: ReadonlySet<string>, key: string) => {
        const next = new Set(set);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
    };
    const whole = report?.database.tables_bytes ?? 0;

    return (
        <AdminTabShell
            title={
                <span className="text-gray-300">
                    Database
                    {report && (
                        <span className="text-gray-500">
                            {" "}
                            · {report.database.name} · {bytes(report.database.size_bytes)} on disk · tables {bytes(whole)} ·
                            read in {report.took_ms} ms
                        </span>
                    )}
                </span>
            }
            onRefresh={() => void load()}
            loading={loading}
            error={error}
        >
            {report && (
                <div className="px-3 sm:px-4 py-2 space-y-4 text-xs">
                    <p className="text-[11px] text-gray-500 max-w-3xl">
                        This database is not the system of record for run history, audit rows or the attribute index; what
                        is older than you need can be pruned. Row counts marked ~ are the planner's estimates. Deleted rows
                        free space for reuse after VACUUM (run after each prune); dropped attribute partitions return their
                        space at once.
                    </p>
                    <div className="overflow-x-auto">
                        <table className="w-full">
                            <thead className="text-[11px] text-gray-500 text-left">
                                <tr>
                                    <th>Area</th>
                                    <th/>
                                    <th className="text-right pr-2">Total</th>
                                    <th className="text-right pr-2 hidden sm:table-cell">Table</th>
                                    <th className="text-right pr-2 hidden sm:table-cell">Indexes</th>
                                    <th className="text-right pr-2 hidden md:table-cell">TOAST</th>
                                    <th className="text-right pr-2">Rows</th>
                                    <th className="hidden md:table-cell">Age</th>
                                    <th>Retention</th>
                                </tr>
                            </thead>
                            <tbody>
                                {report.areas.map((area) => (
                                    <React.Fragment key={area.id}>
                                        <AreaRow
                                            area={area}
                                            whole={whole}
                                            open={open.has(area.id)}
                                            onToggle={() => setOpen(toggle(open, area.id))}
                                            retention={report.retention[area.id]}
                                        />
                                        {open.has(area.id) && (
                                            <tr>
                                                <td colSpan={9} className="pb-3 pl-4">
                                                    <div className="space-y-2">
                                                        <div className="text-[11px] text-gray-400">{area.description}</div>
                                                        <table className="w-full text-[11px]">
                                                            <tbody>
                                                                {area.tables.map((t) => (
                                                                    <React.Fragment key={t.name}>
                                                                    <tr
                                                                        className={`border-t border-gray-800/60 cursor-pointer hover:bg-gray-800/40 ${rowsOf === t.name ? "bg-gray-800/40" : ""}`}
                                                                        onClick={() => setRowsOf(rowsOf === t.name ? null : t.name)}
                                                                        title="Show this table's rows"
                                                                    >
                                                                        <td className="pr-2 text-gray-300 break-all">
                                                                            <span className="text-gray-500 mr-1">{rowsOf === t.name ? "▾" : "▸"}</span>
                                                                            {t.name}
                                                                            {t.partitions ? <span className="text-gray-500"> ({t.partitions} partitions)</span> : null}
                                                                            {t.detached ? <span className="text-amber-300"> (not attached)</span> : null}
                                                                        </td>
                                                                        <td className="pr-2 text-right">{bytes(t.total_bytes)}</td>
                                                                        <td className="pr-2 text-right text-gray-400">tbl {bytes(t.table_bytes)}</td>
                                                                        <td className="pr-2 text-right text-gray-400">idx {bytes(t.index_bytes)}</td>
                                                                        <td className="pr-2 text-right">{formatRows(t.rows, t.rows_exact)}</td>
                                                                        <td className="text-gray-400">
                                                                            {t.age_column ? `${ageRange(t.oldest, t.newest)} (${t.age_column})` : "—"}
                                                                        </td>
                                                                    </tr>
                                                                    {rowsOf === t.name && (
                                                                        <tr>
                                                                            {/* max-w-0: a wide table scrolls inside this cell instead of widening the list. */}
                                                                            <td colSpan={6} className="max-w-0 pb-2 pt-1">
                                                                                <TableRowsView table={t.name} onClose={() => setRowsOf(null)}/>
                                                                            </td>
                                                                        </tr>
                                                                    )}
                                                                    </React.Fragment>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                        {area.id === "attribute_index" && report.attribute_partitions.length > 0 && (
                                                            <PartitionsTable
                                                                partitions={report.attribute_partitions}
                                                                selected={selected}
                                                                onToggle={(key) => setSelected(toggle(selected, key))}
                                                            />
                                                        )}
                                                        {area.prune && (
                                                            <PruneForm_
                                                                area={area}
                                                                selected={area.prune === "collections" ? selected : new Set()}
                                                                onDone={() => {
                                                                    setSelected(new Set());
                                                                    void load();
                                                                }}
                                                            />
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                    </React.Fragment>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <RetentionEditor report={report} onSaved={() => void load()}/>
                </div>
            )}
        </AdminTabShell>
    );
};

export default DatabaseTab;
