import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";

import type {
    DbAreaStorage,
    DbAttributePartition,
    DbAttrProviderShare,
    DbCollectionRef,
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
    groupPartitions,
    groupSelection,
    pruneSpecFor,
    retentionChanges,
    shareLabel,
    sharePercent,
    shareTargets,
    toggleGroupSelection,
    type PruneForm,
    type ShareTarget,
} from "./dbStorage";
import DuplicatesSection from "./DuplicatesSection";
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
//
// An attribute-index partition can also be SHARED into other scopes from its row ("Share with…"):
// a search there reads it in place, beside that scope's own -- for users who can open the
// partition's own scope as well, so a share never shows anyone what they could not already see.
// The partitions are listed per (scope, provider); the group row shares the whole group, the
// collections a later sweep adds included. Collections indexed in more than one scope are listed
// under "Duplicates" (DuplicatesSection.tsx), with the ways to get down to one copy.

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

/** "Share with…": one checkbox per scope the partition (or, for a group, every partition of its
 *  scope and provider -- later ones too) can be shared into; a tick shares it there, an untick
 *  stops. A share only widens where the partition is searched -- a search still reads it only for
 *  a caller who can reach the partition's own scope (db/attribute_index.py). A target the whole
 *  group is shared with is ticked and fixed on a collection's control: untick it on the group. */
const ShareControl: React.FC<{
    ownScope: string;
    sharedWith: readonly string[];
    /** Targets the collection's group is shared with (collection controls only). */
    groupShared?: readonly string[];
    targets: readonly ShareTarget[];
    group?: boolean;
    share: (target: string, on: boolean) => Promise<unknown>;
    onChanged: () => void;
}> = ({ownScope, sharedWith, groupShared = [], targets, group = false, share, onChanged}) => {
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const offered = targets.filter((t) => t.scope !== ownScope);
    const toggle = async (target: string, on: boolean) => {
        setBusy(target);
        setError(null);
        try {
            await share(target, on);
            onChanged();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(null);
        }
    };
    return (
        <details className="relative inline-block">
            <summary
                className="cursor-pointer list-none select-none text-blue-300 hover:text-white"
                title={group ? "Make every collection of this group searchable from other scopes too, later ones included" : "Make this collection searchable from other scopes too"}
            >
                Share with…
            </summary>
            <div className="absolute right-0 z-20 mt-1 w-64 max-w-[calc(100vw-2rem)] rounded-sm border border-gray-700 bg-gray-900 p-1.5 shadow-xl space-y-1">
                <div className="text-[11px] text-gray-500">
                    {group ? "Every collection of the group, and any a later sweep adds, is " : ""}
                    Searched there in place, for users who can also open {ownScope}.
                </div>
                <div className="max-h-56 overflow-auto">
                    {offered.map((t) => {
                        const viaGroup = groupShared.includes(t.scope);
                        return (
                            <label
                                key={t.scope}
                                className="flex items-center gap-2 px-1 py-0.5 text-gray-200 hover:bg-gray-800 rounded-sm cursor-pointer"
                                title={viaGroup ? "shared with the whole group" : t.scope}
                            >
                                <input
                                    type="checkbox"
                                    checked={viaGroup || sharedWith.includes(t.scope)}
                                    disabled={busy !== null || viaGroup}
                                    onChange={(e) => void toggle(t.scope, e.target.checked)}
                                />
                                <span className="truncate">{t.label}</span>
                                {viaGroup && <span className="text-gray-500">(group)</span>}
                                {busy === t.scope && <span className="text-gray-500">…</span>}
                            </label>
                        );
                    })}
                </div>
                {error && <div className="text-red-300">{error}</div>}
            </div>
        </details>
    );
};

const partitionRef = (p: DbAttributePartition): DbCollectionRef | null =>
    p.scope && p.provider && p.collection ? {scope: p.scope, provider: p.provider, collection: p.collection} : null;

/** A checkbox that can show "some ticked". */
const TriCheckbox: React.FC<{state: "all" | "some" | "none"; onChange: () => void; label: string}> = ({state, onChange, label}) => {
    const ref = useRef<HTMLInputElement>(null);
    useEffect(() => {
        if (ref.current) ref.current.indeterminate = state === "some";
    }, [state]);
    return <input ref={ref} type="checkbox" checked={state === "all"} onChange={onChange} aria-label={label}/>;
};

const sharedList = (scopes: readonly string[], targets: readonly ShareTarget[]) =>
    scopes.length ? (
        <span title={scopes.join("\n")}>{scopes.map((s) => shareLabel(s, targets)).join(", ")}</span>
    ) : (
        <span className="text-gray-600">—</span>
    );

/** The attribute index's partitions, one collapsible row per (scope, provider) -- a provider
 *  swept into a scope is one line, its collections under it. The group row shares the whole group
 *  (collections a later sweep adds included) and ticks all its collections for a prune. */
const PartitionsTable: React.FC<{
    partitions: DbAttributePartition[];
    providerShares: readonly DbAttrProviderShare[];
    selected: ReadonlySet<string>;
    onSelect: (next: ReadonlySet<string>) => void;
    targets: readonly ShareTarget[];
    onSharesChanged: () => void;
}> = ({partitions, providerShares, selected, onSelect, targets, onSharesChanged}) => {
    const groups = useMemo(() => groupPartitions(partitions, providerShares), [partitions, providerShares]);
    const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
    return (
        <table className="w-full text-[11px]">
            <thead className="text-gray-500 text-left">
                <tr>
                    <th className="w-6"/>
                    <th>Provider / collection</th>
                    <th>Scope</th>
                    <th>Shared with</th>
                    <th className="text-right">Rows</th>
                    <th className="text-right">Size</th>
                    <th className="text-right">Indexes</th>
                    <th>Ingested</th>
                </tr>
            </thead>
            <tbody>
                {groups.map((g) => {
                    const isOpen = open.has(g.key);
                    const keys = g.partitions.map((p) => partitionRef(p)).filter((r): r is DbCollectionRef => r !== null).map(collectionKey);
                    const flip = () => {
                        const next = new Set(open);
                        if (isOpen) next.delete(g.key);
                        else next.add(g.key);
                        setOpen(next);
                    };
                    return (
                        <React.Fragment key={g.key}>
                            <tr className="border-t border-gray-700 bg-gray-800/30">
                                <td>
                                    {keys.length > 0 && (
                                        <TriCheckbox
                                            state={groupSelection(keys, selected)}
                                            onChange={() => onSelect(toggleGroupSelection(keys, selected))}
                                            label={`select every collection of ${g.provider ?? "?"} in ${g.scope ?? "?"}`}
                                        />
                                    )}
                                </td>
                                <td className="text-gray-200 cursor-pointer select-none" onClick={flip}>
                                    <span className="text-gray-500 mr-1">{isOpen ? "▾" : "▸"}</span>
                                    {g.provider ?? <span className="text-amber-300">no catalogue row</span>}
                                    <span className="text-gray-500"> · {g.partitions.length} collection{g.partitions.length === 1 ? "" : "s"}</span>
                                </td>
                                <td className="text-gray-400 break-all" title={g.scope ?? undefined}>{g.scope ? shareLabel(g.scope, targets) : "—"}</td>
                                <td className="text-gray-400">
                                    {sharedList(g.sharedWith, targets)}
                                    {g.scope && g.provider && (
                                        <span className="ml-2">
                                            <ShareControl
                                                ownScope={g.scope}
                                                sharedWith={g.sharedWith}
                                                targets={targets}
                                                group
                                                share={(t, on) =>
                                                    on
                                                        ? viewerApi.adminDbShareAttrProvider(g.scope as string, g.provider as string, t)
                                                        : viewerApi.adminDbUnshareAttrProvider(g.scope as string, g.provider as string, t)
                                                }
                                                onChanged={onSharesChanged}
                                            />
                                        </span>
                                    )}
                                </td>
                                <td className="text-right">{formatRows(g.rows, g.rowsExact)}</td>
                                <td className="text-right">{bytes(g.totalBytes)}</td>
                                <td/>
                                <td/>
                            </tr>
                            {isOpen &&
                                g.partitions.map((p) => {
                                    const ref = partitionRef(p);
                                    const key = ref ? collectionKey(ref) : p.name;
                                    const own = p.shared_with ?? [];
                                    const viaGroup = p.shared_with_group ?? g.sharedWith;
                                    const all = [...own, ...viaGroup.filter((s) => !own.includes(s))];
                                    return (
                                        <tr key={p.name} className="border-t border-gray-800">
                                            <td className="pl-3">
                                                {ref && (
                                                    <input
                                                        type="checkbox"
                                                        checked={selected.has(key)}
                                                        onChange={() => onSelect(toggleGroupSelection([key], selected))}
                                                        aria-label={`select ${p.provider}/${p.collection}`}
                                                    />
                                                )}
                                            </td>
                                            <td className="text-gray-200 pl-4">
                                                {ref ? p.collection : <span className="text-amber-300">{p.name} (no catalogue row)</span>}
                                                {p.revision ? <span className="text-gray-500"> · {p.revision}</span> : null}
                                            </td>
                                            <td className="text-gray-500 break-all" title={p.scope ?? undefined}>{p.scope ? shareLabel(p.scope, targets) : "—"}</td>
                                            <td className="text-gray-400">
                                                {sharedList(all, targets)}
                                                {ref && (
                                                    <span className="ml-2">
                                                        <ShareControl
                                                            ownScope={ref.scope}
                                                            sharedWith={own}
                                                            groupShared={viaGroup}
                                                            targets={targets}
                                                            share={(t, on) =>
                                                                on
                                                                    ? viewerApi.adminDbShareAttrCollection(ref, t)
                                                                    : viewerApi.adminDbUnshareAttrCollection(ref, t)
                                                            }
                                                            onChanged={onSharesChanged}
                                                        />
                                                    </span>
                                                )}
                                            </td>
                                            <td className="text-right">{formatRows(p.rows, p.rows_exact)}</td>
                                            <td className="text-right">{bytes(p.total_bytes)}</td>
                                            <td className="text-right text-gray-400">{bytes(p.index_bytes)}</td>
                                            <td className="text-gray-400">{fmtTime(p.ingested_at)}</td>
                                        </tr>
                                    );
                                })}
                        </React.Fragment>
                    );
                })}
            </tbody>
        </table>
    );
};

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

    // Where a partition can be shared: Shared, the admin's own scope, every project. Read once; a
    // failure only leaves the "Share with…" lists shorter (Shared is always there).
    const [targets, setTargets] = useState<ShareTarget[]>(shareTargets(null, [], null));
    useEffect(() => {
        let live = true;
        void Promise.allSettled([viewerApi.me(), viewerApi.adminListProjects()]).then(([me, projects]) => {
            if (!live) return;
            setTargets(
                shareTargets(
                    me.status === "fulfilled" ? me.value.sub : null,
                    projects.status === "fulfilled" ? projects.value : [],
                    null,
                ),
            );
        });
        return () => {
            live = false;
        };
    }, []);

    const toggle =(set: ReadonlySet<string>, key: string) => {
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
                                                                providerShares={report.attribute_provider_shares ?? []}
                                                                selected={selected}
                                                                onSelect={setSelected}
                                                                targets={targets}
                                                                onSharesChanged={() => void load()}
                                                            />
                                                        )}
                                                        {area.id === "attribute_index" && report.attribute_duplicates && (
                                                            <DuplicatesSection
                                                                duplicates={report.attribute_duplicates}
                                                                targets={targets}
                                                                onDone={() => {
                                                                    setSelected(new Set());
                                                                    void load();
                                                                }}
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
