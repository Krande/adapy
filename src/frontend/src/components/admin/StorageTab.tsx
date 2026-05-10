import React, {useEffect, useRef, useState} from "react";
import {AdminFileEntry, ApiError, TargetFormat, viewerApi} from "@/services/viewerApi";
import {ensureConverted} from "@/services/conversion";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {runtime} from "@/runtime/config";

// Admin-only enriched storage view. Shows source format, size, upload
// time, and the derived blobs already cached for each source. Houses
// the DL/Convert/Delete actions that used to live in the regular
// StorageBrowser — those are admin-power-user features, not
// everyday-user features.
//
// Targets the *currently-selected scope* (read from scopeStore). To
// audit a different scope, the admin switches scope in the options
// drawer first; the table refetches.

type OverrideKey =
    | "use_sat_pcurves"
    | "pcurve_drive_edge"
    | "skip_shapefix"
    | "merge_meshes"
    | "profile_conversions";

type OverrideTri = "unset" | "on" | "off";

const OVERRIDE_KEYS: { key: OverrideKey; label: string }[] = [
    {key: "use_sat_pcurves", label: "Use SAT pcurves"},
    {key: "pcurve_drive_edge", label: "Drive edge from pcurve"},
    {key: "skip_shapefix", label: "Skip ShapeFix"},
    {key: "merge_meshes", label: "Merge GLB meshes"},
    {key: "profile_conversions", label: "Profile this run"},
];

function buildConversionOptions(
    o: Record<OverrideKey, OverrideTri>,
): Partial<Record<OverrideKey, boolean | null>> | undefined {
    const out: Partial<Record<OverrideKey, boolean | null>> = {};
    let any = false;
    for (const k of Object.keys(o) as OverrideKey[]) {
        const v = o[k];
        if (v === "on") {
            out[k] = true;
            any = true;
        } else if (v === "off") {
            out[k] = false;
            any = true;
        }
        // "unset" → omit the key, so the global setting wins.
    }
    return any ? out : undefined;
}

const StorageTab: React.FC = () => {
    const currentScope = useScopeStore((s) => s.current);
    const scope = scopeUrlPart(currentScope);
    const [files, setFiles] = useState<AdminFileEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busyKey, setBusyKey] = useState<string | null>(null);
    const [expandedKey, setExpandedKey] = useState<string | null>(null);
    // Multi-select for batch operations (currently just move-to-folder).
    // Tracks source keys; derived-blob rows aren't selectable since
    // moving a derived blob directly is rejected by the backend.
    const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set());
    const toggleKeySelection = (key: string) => {
        setSelectedKeys((prev) => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    };
    const clearSelection = () => setSelectedKeys(new Set());
    // Drop the selection when the scope changes — selecting in scope A
    // and moving in scope B would silently no-op (keys live per scope).
    useEffect(() => {
        clearSelection();
    }, [scope]);
    const [overrideOpen, setOverrideOpen] = useState(false);
    const [overrides, setOverrides] = useState<Record<OverrideKey, OverrideTri>>(() =>
        OVERRIDE_KEYS.reduce(
            (acc, {key}) => ({...acc, [key]: "unset"}),
            {} as Record<OverrideKey, OverrideTri>,
        ),
    );
    const activeOverrides = OVERRIDE_KEYS.filter(({key}) => overrides[key] !== "unset").length;

    // Track the in-flight reload so a second tap can supersede it
    // instead of being silently ignored. Without this, a hung
    // request would leave the button disabled forever and the user
    // would believe "nothing happened" — exactly the symptom we're
    // fixing here.
    const inflightRef = useRef<{seq: number; cancel: AbortController} | null>(null);
    const reloadSeq = useRef(0);
    // Min visible busy duration. The endpoint is ~30 ms; without a
    // floor the spinner blinks too fast for the eye to register and
    // the click feels like it did nothing.
    const MIN_BUSY_MS = 250;

    const reload = async () => {
        // Supersede any in-flight reload — a second tap means the
        // user wants fresh data right now, not the previous attempt.
        if (inflightRef.current) {
            inflightRef.current.cancel.abort();
        }
        const seq = ++reloadSeq.current;
        const cancel = new AbortController();
        inflightRef.current = {seq, cancel};
        setLoading(true);
        const startedAt = Date.now();
        try {
            const files = await viewerApi.adminListStorage(scope, {signal: cancel.signal});
            // Only the latest reload commits results — race-safe.
            if (reloadSeq.current === seq) {
                setFiles(files);
                setError(null);
            }
        } catch (e: unknown) {
            // Aborted requests aren't errors; the superseding tap
            // owns the UI now.
            if ((e as {name?: string}).name === "AbortError") return;
            if (reloadSeq.current === seq) {
                setError(e instanceof ApiError ? e.detail || e.message : String(e));
            }
        } finally {
            // Hold the busy state long enough for a person to see
            // the click registered, even on a 30 ms response.
            const elapsed = Date.now() - startedAt;
            const wait = Math.max(0, MIN_BUSY_MS - elapsed);
            if (wait > 0) await new Promise((r) => setTimeout(r, wait));
            if (reloadSeq.current === seq) {
                setLoading(false);
                inflightRef.current = null;
            }
        }
    };

    useEffect(() => {
        void reload();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope]);

    const onDownload = async (key: string, suggestedName: string) => {
        try {
            await viewerApi.downloadBlob(scope, key, suggestedName);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        }
    };

    const onConvert = async (sourceKey: string, target: TargetFormat) => {
        const stateKey = `${sourceKey}::${target}`;
        setBusyKey(stateKey);
        setError(null);
        try {
            const conversionOptions = buildConversionOptions(overrides);
            const derivedKey = await ensureConverted(scope, sourceKey, target, {
                conversionOptions,
            });
            const base = sourceKey.replace(/\.[^./]+$/, "");
            await viewerApi.downloadBlob(scope, derivedKey, `${base}.${target}`);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusyKey(null);
        }
    };

    const onDelete = async (key: string, label: string) => {
        if (!confirm(`Delete "${label}"? Any derived products are removed too.`)) return;
        setBusyKey(`${key}::delete`);
        try {
            await viewerApi.adminDeleteBlob(scope, key);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusyKey(null);
        }
    };

    const onMoveSelectedToFolder = async () => {
        if (selectedKeys.size === 0) return;
        const folder = window.prompt(
            `Move ${selectedKeys.size} file${selectedKeys.size === 1 ? "" : "s"} to folder:`,
            "",
        );
        if (folder === null) return; // cancelled
        const trimmed = folder.trim().replace(/^\/+|\/+$/g, "");
        if (!trimmed) {
            setError("Folder name required");
            return;
        }
        setBusyKey("__bulk_move__");
        setError(null);
        try {
            const result = await viewerApi.adminMoveKeysToFolder(
                scope,
                Array.from(selectedKeys),
                trimmed,
            );
            if (result.failed.length > 0) {
                const summary = result.failed
                    .map((f) => `${f.key}: ${f.reason}`)
                    .join("\n");
                setError(
                    `Moved ${result.moved.length} of ${
                        result.moved.length + result.failed.length
                    }; failures:\n${summary}`,
                );
            }
            clearSelection();
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusyKey(null);
        }
    };

    // Delete a single derived blob without touching the source.
    // Common during conversion debugging: remove the cached GLB so
    // the next /convert re-runs the pipeline instead of returning the
    // cached output. The admin endpoint already routes derived keys
    // to a one-blob delete; the source delete path is unaffected.
    const onDeleteDerived = async (sourceKey: string, derivedKey: string, label: string) => {
        if (!confirm(`Delete cached "${label}"? Next Convert will regenerate it.`)) return;
        setBusyKey(`${derivedKey}::delete`);
        try {
            await viewerApi.adminDeleteBlob(scope, derivedKey);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusyKey(null);
        }
    };

    return (
        <div className="flex flex-col h-full">
            <div className="flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-gray-700 text-xs">
                <span className="text-gray-400">
                    Scope: <span className="text-white">{currentScope?.name ?? "Shared"}</span>
                </span>
                <span className="text-gray-500">·</span>
                <span className="text-gray-400">{files.length} source{files.length === 1 ? "" : "s"}</span>
                <button
                    className="ml-2 bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded text-[11px]"
                    onClick={() => setOverrideOpen((v) => !v)}
                    title="Per-conversion overrides applied to all Convert clicks on this tab"
                >
                    Overrides{activeOverrides ? ` (${activeOverrides})` : ""} {overrideOpen ? "▾" : "▸"}
                </button>
                {selectedKeys.size > 0 && (
                    <>
                        <span className="ml-2 text-gray-300">
                            {selectedKeys.size} selected
                        </span>
                        <button
                            type="button"
                            className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded text-[11px] disabled:opacity-50"
                            onClick={() => void onMoveSelectedToFolder()}
                            disabled={busyKey === "__bulk_move__"}
                            title="Rename selected sources under a folder prefix"
                        >
                            {busyKey === "__bulk_move__" ? "Moving…" : "Move to folder…"}
                        </button>
                        <button
                            type="button"
                            className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded text-[11px]"
                            onClick={clearSelection}
                            title="Clear selection"
                        >
                            Clear
                        </button>
                    </>
                )}
                <button
                    type="button"
                    className={
                        "ml-auto inline-flex items-center gap-1.5 bg-blue-700 active:bg-blue-800 " +
                        "hover:bg-blue-600 rounded text-xs " +
                        // Bigger tap target on mobile (≥40px tall);
                        // tighter on desktop where the cursor is precise.
                        "px-4 py-2 sm:px-3 sm:py-1 min-h-[40px] sm:min-h-0 " +
                        // Visible "active" ring to confirm the tap
                        // landed even before the network call returns.
                        "focus:outline-none focus:ring-2 focus:ring-blue-400 " +
                        (loading ? "opacity-90 cursor-wait" : "")
                    }
                    onClick={() => void reload()}
                    aria-busy={loading}
                    title={loading ? "Refreshing — tap again to retry" : "Refresh storage list"}
                >
                    {/* Inline spinner so feedback is visual on mobile,
                        not just a text swap. The label stays "Refresh"
                        so the user can re-tap to abort+retry without
                        wondering whether the button is disabled. */}
                    <svg
                        className={"h-3 w-3 " + (loading ? "animate-spin" : "opacity-60")}
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                    >
                        <path d="M21 12a9 9 0 1 1-3.5-7.1"/>
                        <polyline points="21 4 21 10 15 10"/>
                    </svg>
                    Refresh
                </button>
            </div>
            {overrideOpen && (
                <div className="px-3 sm:px-4 py-2 border-b border-gray-700 bg-gray-900/40 text-[11px]">
                    <div className="text-gray-400 mb-2">
                        Overrides apply to every Convert click on this tab. ``Unset`` →
                        worker uses the global setting (or adapy's code default).
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {OVERRIDE_KEYS.map(({key, label}) => (
                            <div key={key} className="flex items-center gap-2">
                                <span className="flex-1 truncate" title={key}>{label}</span>
                                <div className="inline-flex rounded overflow-hidden">
                                    {(["unset", "on", "off"] as OverrideTri[]).map((v) => (
                                        <button
                                            key={v}
                                            onClick={() => setOverrides((o) => ({...o, [key]: v}))}
                                            className={
                                                "px-2 py-0.5 border text-[10px] " +
                                                (overrides[key] === v
                                                    ? "bg-blue-700 text-white border-blue-500"
                                                    : "bg-gray-800 text-gray-200 border-gray-700 hover:bg-gray-700")
                                            }
                                        >
                                            {v === "unset" ? "—" : v === "on" ? "On" : "Off"}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {error && (
                <div className="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <div className="flex-1 overflow-auto">
                {/* Desktop table.
                    Min-width keeps columns readable even when the
                    parent shrinks (narrow window, tree panel open) —
                    the surrounding overflow-auto then provides a
                    horizontal scrollbar instead of squishing everything
                    into unreadable mush. */}
                <table className="hidden sm:table w-full text-sm table-fixed min-w-[1200px]">
                    <colgroup>
                        <col className="w-[2.5rem]"/>
                        <col className="w-[24rem]"/>
                        <col className="w-[10rem]"/>
                        <col className="w-[7rem]"/>
                        <col className="w-[12rem]"/>
                        <col/>
                        <col className="w-[18rem]"/>
                    </colgroup>
                    <thead className="sticky top-0 bg-gray-800 text-left">
                    <tr>
                        <Th>{""}</Th>
                        <Th>Name</Th>
                        <Th>Format</Th>
                        <Th>Size</Th>
                        <Th>Uploaded</Th>
                        <Th>Derived products</Th>
                        <Th>{""}</Th>
                    </tr>
                    </thead>
                    <tbody>
                    {files.map((f) => (
                        <SourceRow
                            key={f.key}
                            file={f}
                            busyKey={busyKey}
                            scope={scope}
                            selected={selectedKeys.has(f.key)}
                            onToggleSelected={() => toggleKeySelection(f.key)}
                            onConvert={onConvert}
                            onDownload={onDownload}
                            onDelete={onDelete}
                            onDeleteDerived={onDeleteDerived}
                        />
                    ))}
                    </tbody>
                </table>
                {/* Mobile cards */}
                <ul className="sm:hidden divide-y divide-gray-800">
                    {files.map((f) => (
                        <SourceCard
                            key={f.key}
                            file={f}
                            busyKey={busyKey}
                            expanded={expandedKey === f.key}
                            selected={selectedKeys.has(f.key)}
                            onToggleSelected={() => toggleKeySelection(f.key)}
                            onToggleExpand={() => setExpandedKey(expandedKey === f.key ? null : f.key)}
                            onConvert={onConvert}
                            onDownload={onDownload}
                            onDelete={onDelete}
                            onDeleteDerived={onDeleteDerived}
                        />
                    ))}
                </ul>
                {!loading && files.length === 0 && (
                    <div className="px-4 py-8 text-center text-gray-500 text-sm">
                        No files in this scope.
                    </div>
                )}
            </div>
        </div>
    );
};

interface RowProps {
    file: AdminFileEntry;
    busyKey: string | null;
    selected: boolean;
    onToggleSelected: () => void;
    onConvert: (sourceKey: string, target: TargetFormat) => void;
    onDownload: (key: string, suggestedName: string) => void;
    onDelete: (key: string, label: string) => void;
    onDeleteDerived: (sourceKey: string, derivedKey: string, label: string) => void;
}

const SourceRow: React.FC<RowProps & {scope: string}> = ({
    file,
    busyKey,
    selected,
    onToggleSelected,
    onConvert,
    onDownload,
    onDelete,
    onDeleteDerived,
}) => {
    const downloadable = file.available_targets.filter((t) => t !== "glb");
    const busyConverting = busyKey?.startsWith(`${file.key}::`) && !busyKey.endsWith("::delete");
    const busyDeleting = busyKey === `${file.key}::delete`;
    return (
        <tr className="border-t border-gray-800 align-top">
            <Td>
                <input
                    type="checkbox"
                    checked={selected}
                    onChange={onToggleSelected}
                    aria-label={`Select ${file.key}`}
                    disabled={file.orphan === true}
                    title={file.orphan ? "Orphans can't be moved" : "Select for batch operations"}
                />
            </Td>
            <Td title={file.key}>
                {file.orphan && (
                    <span className="text-[10px] uppercase text-yellow-400 mr-1" title="Source missing">
                        orphan
                    </span>
                )}
                {file.key}
            </Td>
            <Td>{file.format}</Td>
            <Td>{formatBytes(file.size)}</Td>
            <Td title={file.last_modified || ""}>
                {file.last_modified ? file.last_modified.replace("T", " ").slice(0, 19) : "—"}
            </Td>
            <Td>
                <div className="flex flex-wrap gap-1">
                    {file.derived.length === 0 && <span className="text-gray-500">—</span>}
                    {file.derived.map((d) => {
                        const busyDerived = busyKey === `${d.key}::delete`;
                        return (
                            <span key={d.key} className="inline-flex rounded overflow-hidden border border-gray-700">
                                <button
                                    className="bg-gray-800 hover:bg-gray-700 px-2 py-0.5 text-[11px]"
                                    onClick={() => onDownload(d.key, suggestedName(file.key, d.format))}
                                    title={`${d.key} (${formatBytes(d.size)})`}
                                >
                                    {d.format.toUpperCase()} ↓
                                </button>
                                <button
                                    className="bg-red-900/70 hover:bg-red-800 px-1.5 text-[11px] text-gray-100 disabled:opacity-50"
                                    onClick={() => onDeleteDerived(file.key, d.key, `${file.key} → ${d.format}`)}
                                    disabled={busyDerived}
                                    title="Delete cached derived blob (next Convert will regenerate it)"
                                >
                                    {busyDerived ? "…" : "×"}
                                </button>
                            </span>
                        );
                    })}
                </div>
            </Td>
            <Td>
                <div className="flex flex-wrap gap-1 justify-end">
                    {!file.orphan && (
                        <button
                            className="bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded text-xs"
                            onClick={() => onDownload(file.key, file.key)}
                        >
                            DL
                        </button>
                    )}
                    {!file.orphan && runtime.convertEnabled() && downloadable.length > 0 && (
                        <select
                            disabled={busyConverting || false}
                            className="bg-gray-700 hover:bg-gray-600 text-xs rounded px-1 py-0.5 disabled:opacity-50"
                            value=""
                            onChange={(e) => {
                                const t = e.target.value as TargetFormat | "";
                                e.target.value = "";
                                if (t) onConvert(file.key, t);
                            }}
                        >
                            <option value="">{busyConverting ? "…" : "Convert ▾"}</option>
                            {downloadable.map((t) => (
                                <option key={t} value={t}>{t.toUpperCase()}</option>
                            ))}
                        </select>
                    )}
                    <button
                        className="bg-red-800 hover:bg-red-700 px-2 py-0.5 rounded text-xs disabled:opacity-50"
                        onClick={() => onDelete(file.key, file.key)}
                        disabled={busyDeleting}
                        title="Delete source + all derived"
                    >
                        {busyDeleting ? "…" : "Delete"}
                    </button>
                </div>
            </Td>
        </tr>
    );
};

interface CardProps extends RowProps {
    expanded: boolean;
    onToggleExpand: () => void;
}

const SourceCard: React.FC<CardProps> = ({
    file,
    busyKey,
    expanded,
    selected,
    onToggleSelected,
    onToggleExpand,
    onConvert,
    onDownload,
    onDelete,
    onDeleteDerived,
}) => {
    const downloadable = file.available_targets.filter((t) => t !== "glb");
    const busyConverting = busyKey?.startsWith(`${file.key}::`) && !busyKey.endsWith("::delete");
    const busyDeleting = busyKey === `${file.key}::delete`;
    return (
        <li className="px-3 py-3 text-xs">
            <div className="flex items-start gap-2">
                <input
                    type="checkbox"
                    checked={selected}
                    onChange={onToggleSelected}
                    onClick={(e) => e.stopPropagation()}
                    className="mt-1 shrink-0"
                    aria-label={`Select ${file.key}`}
                    disabled={file.orphan === true}
                    title={file.orphan ? "Orphans can't be moved" : "Select for batch operations"}
                />
                <button
                    type="button"
                    className="flex-1 min-w-0 text-left"
                    onClick={onToggleExpand}
                >
                    <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-sm truncate" title={file.key}>
                            {file.orphan && (
                                <span className="text-[10px] uppercase text-yellow-400 mr-1">orphan</span>
                            )}
                            {file.key}
                        </span>
                        <span className="text-[11px] text-gray-400 shrink-0">{formatBytes(file.size)}</span>
                    </div>
                    <div className="text-gray-400 mt-0.5">
                        {file.format}
                        {file.last_modified ? ` · ${file.last_modified.slice(0, 10)}` : ""}
                        {file.derived.length > 0 ? ` · ${file.derived.length} derived` : ""}
                    </div>
                </button>
            </div>
            {expanded && (
                <div className="mt-2 space-y-2">
                    {file.derived.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                            {file.derived.map((d) => {
                                const busyDerived = busyKey === `${d.key}::delete`;
                                return (
                                    <span key={d.key} className="inline-flex rounded overflow-hidden border border-gray-700">
                                        <button
                                            className="bg-gray-800 hover:bg-gray-700 px-2 py-0.5 text-[11px]"
                                            onClick={() => onDownload(d.key, suggestedName(file.key, d.format))}
                                            title={`${d.key} (${formatBytes(d.size)})`}
                                        >
                                            {d.format.toUpperCase()} ↓
                                        </button>
                                        <button
                                            className="bg-red-900/70 hover:bg-red-800 px-1.5 text-[11px] text-gray-100 disabled:opacity-50"
                                            onClick={() => onDeleteDerived(file.key, d.key, `${file.key} → ${d.format}`)}
                                            disabled={busyDerived}
                                            title="Delete cached derived blob"
                                        >
                                            {busyDerived ? "…" : "×"}
                                        </button>
                                    </span>
                                );
                            })}
                        </div>
                    )}
                    <div className="flex flex-wrap gap-1">
                        {!file.orphan && (
                            <button
                                className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded text-xs"
                                onClick={() => onDownload(file.key, file.key)}
                            >
                                Download
                            </button>
                        )}
                        {!file.orphan && runtime.convertEnabled() && downloadable.length > 0 && (
                            <select
                                disabled={busyConverting || false}
                                className="bg-gray-700 hover:bg-gray-600 text-xs rounded px-2 py-1 disabled:opacity-50"
                                value=""
                                onChange={(e) => {
                                    const t = e.target.value as TargetFormat | "";
                                    e.target.value = "";
                                    if (t) onConvert(file.key, t);
                                }}
                            >
                                <option value="">{busyConverting ? "…" : "Convert ▾"}</option>
                                {downloadable.map((t) => (
                                    <option key={t} value={t}>{t.toUpperCase()}</option>
                                ))}
                            </select>
                        )}
                        <button
                            className="bg-red-800 hover:bg-red-700 px-2 py-1 rounded text-xs disabled:opacity-50"
                            onClick={() => onDelete(file.key, file.key)}
                            disabled={busyDeleting}
                        >
                            {busyDeleting ? "…" : "Delete"}
                        </button>
                    </div>
                </div>
            )}
        </li>
    );
};

const Th: React.FC<{children: React.ReactNode}> = ({children}) => (
    <th className="px-3 py-2 font-medium text-gray-300 whitespace-nowrap">{children}</th>
);

const Td: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
    <td className="px-3 py-2 truncate" title={title}>
        {children}
    </td>
);

function formatBytes(n: number): string {
    if (!n) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function suggestedName(sourceKey: string, target: string): string {
    const base = sourceKey.replace(/\.[^./]+$/, "");
    return `${base}.${target}`;
}

export default StorageTab;
