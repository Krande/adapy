// "Changes": a provider's change checks for the open collection, in the Sources tab.
//
// Shown only when a live provider serving this collection declares a `change-check` entry in its
// `asset_schedules` (the asset-provider seam). From here:
//
//   * "Check for changes" runs it now (`POST /asset-changes/check`; the server builds the job
//     options from the live declaration and this scope's scheduled check's settings);
//   * the run picker lists every check, scheduled or manual, newest first;
//   * picking a run draws its nodes on the tree (the purple +/~/- evidence marks, for this run
//     only -- see `@/assets/changeRuns`) and lists them as rows; a row selects its node.
//
// Report only: nothing here publishes. Re-requesting the tree or geometry is how a change is
// picked up, exactly as before.

import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
    filterRunItems,
    itemsByUser,
    runLabel,
    runMarks,
    treeAction,
    type RunItem,
} from "@/assets/changeRuns";
import type { ChangeAction } from "@/assets/changes";
import { assetProviderCollections, type AssetProviderCollections } from "@/services/assetScopeCollections";
import type { ChangeItem, ChangeRun } from "@/services/viewerApi";
import { ApiError, viewerApi } from "@/services/viewerApi";
import { useMeStore } from "@/state/meStore";
import { useProviderName } from "@/state/providerNamesStore";

const POLL_MS = 5000;
const MAX_ROWS = 500;

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));

const ACTION_STYLE: Record<ChangeAction, string> = {
    added: "text-emerald-300",
    modified: "text-violet-300",
    deleted: "text-red-300",
};

const ChangeRunsPanel: React.FC<{
    scope: string;
    collection: string | null;
    /** The picked run's marks for the tree, or null to show the change feed again. */
    onMarks: (marks: ReadonlyMap<string, ChangeAction> | null) => void;
    /** A row was clicked: select its node in the tree. */
    onSelectNode: (node: string, name: string | null) => void;
}> = ({ scope, collection, onMarks, onSelectNode }) => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const pn = useProviderName();
    const [providers, setProviders] = useState<AssetProviderCollections[]>([]);
    const [providerId, setProviderId] = useState("");
    const [runs, setRuns] = useState<ChangeRun[]>([]);
    const [runId, setRunId] = useState("");
    const [items, setItems] = useState<ChangeItem[] | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [user, setUser] = useState("");
    const [action, setAction] = useState("");
    const [query, setQuery] = useState("");

    // Which providers can check THIS collection. A provider advertises its collections in its own
    // spelling (often upper case); the published collection is its lower case.
    useEffect(() => {
        let alive = true;
        void viewerApi
            .listBackendPlugins()
            .catch(() => ({ plugins: [] }))
            .then((r) => {
                if (!alive) return;
                const checkable = assetProviderCollections(r.plugins ?? []).filter((p) => p.changeCheck);
                setProviders(checkable);
            });
        return () => {
            alive = false;
        };
    }, []);

    const serving = useMemo(
        () =>
            collection
                ? providers
                      .map((p) => ({ p, advertised: p.collections.find((c) => c.toLowerCase() === collection.toLowerCase()) }))
                      .filter((x): x is { p: AssetProviderCollections; advertised: string } => !!x.advertised)
                : [],
        [providers, collection],
    );
    useEffect(() => {
        if (!serving.some((s) => s.p.providerId === providerId)) setProviderId(serving[0]?.p.providerId ?? "");
    }, [serving, providerId]);
    const current = serving.find((s) => s.p.providerId === providerId) ?? null;

    const loadRuns = useCallback(async () => {
        if (!current) return;
        try {
            const list = await viewerApi.listAssetChangeRuns(scope, {
                provider: current.p.providerId,
                collection: current.advertised,
                limit: 50,
            });
            setRuns(list);
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
    }, [scope, current]);

    useEffect(() => {
        setRuns([]);
        setRunId("");
        void loadRuns();
    }, [loadRuns]);

    // A queued run finishes on the server when a listing sees its job done, so keep listing.
    const pending = runs.some((r) => r.status === "queued");
    useEffect(() => {
        if (!pending) return;
        const t = window.setInterval(() => void loadRuns(), POLL_MS);
        return () => window.clearInterval(t);
    }, [pending, loadRuns]);

    const run = runs.find((r) => r.id === runId) ?? null;

    useEffect(() => {
        setItems(null);
        setUser("");
        setAction("");
        setQuery("");
        if (!run || run.status !== "done" || !run.has_items) {
            onMarks(null);
            if (run?.status === "done") setItems([]);
            return;
        }
        let alive = true;
        void viewerApi
            .assetChangeRunItems(scope, run.id)
            .then((r) => {
                if (!alive) return;
                setItems(r.items);
                onMarks(runMarks(r.items as RunItem[]));
            })
            .catch((e) => alive && setError(errText(e)));
        return () => {
            alive = false;
        };
        // `onMarks` is the tab's setter; re-running for a new identity would refetch for nothing.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, run?.id, run?.status]);

    // Leaving the collection (or unmounting) drops the run's marks.
    useEffect(() => () => onMarks(null), [collection]); // eslint-disable-line react-hooks/exhaustive-deps

    const shown = useMemo(
        () => (items ? filterRunItems(items as RunItem[], { user, action, query }) : []),
        [items, user, action, query],
    );
    const users = useMemo(() => (items ? itemsByUser(items as RunItem[]) : []), [items]);

    if (!current) return null;
    const check = current.p.changeCheck!;
    const blocked = check.requiresAdmin && !isAdmin;
    const latest = runs[0] ?? null;

    const onCheck = async () => {
        setBusy(true);
        setError(null);
        try {
            const started = await viewerApi.checkAssetChanges(scope, current.p.providerId, current.advertised);
            await loadRuns();
            setRunId(started.id);
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <details className="px-2 py-1.5 border-b border-gray-700/70 text-xs" data-testid="change-runs-panel">
            <summary className="cursor-pointer text-gray-400 select-none">
                Changes
                {latest && (
                    <span className={latest.stale ? "text-amber-300" : "text-gray-500"}>
                        {" "}
                        — last check {runLabel(latest, fmt)}
                    </span>
                )}
            </summary>
            <div className="mt-1.5 space-y-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                    {serving.length > 1 && (
                        <select
                            className="bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5"
                            value={providerId}
                            onChange={(e) => setProviderId(e.target.value)}
                            aria-label="Provider"
                        >
                            {serving.map((s) => (
                                <option key={s.p.providerId} value={s.p.providerId}>
                                    {pn(s.p.providerId)}
                                </option>
                            ))}
                        </select>
                    )}
                    <button
                        type="button"
                        className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm"
                        onClick={() => void onCheck()}
                        disabled={busy || blocked}
                        title={blocked ? `${pn(current.p.providerId)} change checks are restricted to admins` : check.description ?? check.label}
                    >
                        {busy ? "Starting…" : "Check for changes"}
                    </button>
                    <select
                        className="bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5 min-w-0 flex-1"
                        value={runId}
                        onChange={(e) => setRunId(e.target.value)}
                        aria-label="Change-check run"
                    >
                        <option value="">{runs.length ? "Pick a run to show it on the tree…" : "No checks yet"}</option>
                        {runs.map((r) => (
                            <option key={r.id} value={r.id}>
                                {runLabel(r, fmt)}
                            </option>
                        ))}
                    </select>
                </div>
                {error && <div className="text-red-300">{error}</div>}
                {run && (
                    <div className="space-y-1">
                        <div className={run.status === "error" ? "text-red-300" : run.stale ? "text-amber-300" : "text-gray-300"}>
                            {run.status === "queued"
                                ? "Running — this updates when the check finishes."
                                : run.status === "error"
                                  ? run.error || "The check failed."
                                  : run.message || (run.up_to_date ? "Up to date." : "Changes found.")}
                        </div>
                        {items && items.length > 0 && (
                            <>
                                <div className="flex flex-wrap items-center gap-1.5">
                                    <select
                                        className="bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5"
                                        value={user}
                                        onChange={(e) => setUser(e.target.value)}
                                        aria-label="Filter by user"
                                    >
                                        <option value="">All users</option>
                                        {users.map(([u, n]) => (
                                            <option key={u} value={u}>
                                                {u} ({n})
                                            </option>
                                        ))}
                                    </select>
                                    <select
                                        className="bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5"
                                        value={action}
                                        onChange={(e) => setAction(e.target.value)}
                                        aria-label="Filter by change"
                                    >
                                        <option value="">All changes</option>
                                        <option value="added">Added</option>
                                        <option value="modified">Modified</option>
                                        <option value="deleted">Deleted</option>
                                    </select>
                                    <input
                                        className="bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5 min-w-0 flex-1"
                                        placeholder="Search name or id…"
                                        value={query}
                                        onChange={(e) => setQuery(e.target.value)}
                                    />
                                    <span className="text-gray-500 whitespace-nowrap">
                                        {shown.length} of {items.length}
                                    </span>
                                </div>
                                <ul className="max-h-64 overflow-auto divide-y divide-gray-800 rounded-sm border border-gray-800">
                                    {shown.slice(0, MAX_ROWS).map((i, n) => {
                                        const mark = treeAction(i.action);
                                        return (
                                            <li key={`${i.node ?? "none"}-${n}`}>
                                                <button
                                                    type="button"
                                                    className="w-full text-left px-1.5 py-1 hover:bg-gray-800 disabled:cursor-default disabled:hover:bg-transparent"
                                                    disabled={!i.node}
                                                    onClick={() => i.node && onSelectNode(i.node, i.name)}
                                                    title={i.node ? `Select ${i.node} in the tree` : "Not published in this scope yet"}
                                                >
                                                    <div className="flex items-center gap-1.5 min-w-0">
                                                        <span className={`w-14 shrink-0 ${ACTION_STYLE[mark]}`}>{i.action}</span>
                                                        <span className="truncate text-gray-200">{i.name || i.node || "—"}</span>
                                                    </div>
                                                    {(i.changed_by || i.changed_at || i.detail) && (
                                                        <div className="pl-[3.875rem] text-[11px] text-gray-500 truncate">
                                                            {[i.changed_by, i.changed_at ? fmt(i.changed_at) : null, i.detail]
                                                                .filter(Boolean)
                                                                .join(" · ")}
                                                        </div>
                                                    )}
                                                </button>
                                            </li>
                                        );
                                    })}
                                    {shown.length > MAX_ROWS && (
                                        <li className="px-1.5 py-1 text-gray-500">
                                            {shown.length - MAX_ROWS} more — narrow the filter to see them.
                                        </li>
                                    )}
                                </ul>
                            </>
                        )}
                    </div>
                )}
            </div>
        </details>
    );
};

export default ChangeRunsPanel;
