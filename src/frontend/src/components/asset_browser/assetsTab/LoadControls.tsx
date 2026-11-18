// The Load buttons of the Sources tab: one node's, a selection's (grouped by provider), and a
// tree set's, which asks first which members have geometry to load.

import React, { useMemo, useState } from "react";
import type { AssetView } from "@/assets/assetView";
import { geometryIndex, rowLoadable } from "@/assets/geometryMarks";
import { providerIdTitle } from "@/assets/providerNames";
import { rowFacts } from "@/assets/rowFacts";
import { loadsProvider, type TreeSet } from "@/assets/treeSets";
import { beginOperation, type Operation } from "@/services/operationTracking";
import { useProviderName } from "@/state/providerNamesStore";
import { useViewerStores } from "@/state/AdaViewerContext";
import { formatRevision } from "../format";
import { NOTHING_TO_LOAD, NOTHING_TO_LOAD_ANY, loadGroups, loadKey, loadSelection, plural, requestLoadsFor, requestThenLoadInto, useAssetLoads, type AssetLoadControl, type LoadGroup, type NodeRequestControl, type RequestLoadControl, type RequestMissing } from "./loads";
import { BTN_PRIMARY, BTN_QUIET, BTN_SECONDARY } from "./chrome";

/** "Load set": every member's geometry, from the providers the member chose, as ONE bulk load.
 *
 *  The same controls a selection's load uses (`useAssetLoads`), so what loads, how it is named in
 *  the scene and how it reports a failure are the tree's own.
 *
 *  ASKED FIRST, NOT DISCOVERED BY FAILING. Each (member, provider) pair is checked against that
 *  provider's published geometry before anything loads. A provider that published only the TREE
 *  has a claim on every row but nothing to load, and attempting it fails once per member. When any
 *  pair has no geometry, the load stops at a prompt: request the missing ones (and load each as its
 *  publish arrives), load only what is there, or cancel. */
export const SetLoad: React.FC<{ view: AssetView; set: TreeSet; scope: string; requestMissing?: RequestMissing }> = ({
    view,
    set,
    scope,
    requestMissing,
}) => {
    const { useAssetBrowserStore } = useViewerStores();
    const pn = useProviderName();
    const rollup = useAssetBrowserStore((s) => s.geometryRollup);
    const [asking, setAsking] = useState(false);
    const present = set.members.filter((m) => view.hierarchy.byId.has(m.id));
    const controls = useAssetLoads(
        view,
        present.map((m) => m.id),
        scope,
    );
    // One geometry index per provider, for every provider a member could have chosen.
    const indexes = useMemo(
        () => new Map(view.contentProviders.map((p) => [p, geometryIndex(view, p, rollup)] as const)),
        [view, rollup],
    );
    const loadable = (id: string, provider: string) => {
        const idx = indexes.get(provider);
        return !!idx && rowLoadable(view, idx, id);
    };
    const byId = new Map(present.map((m) => [m.id, m]));
    const chosen = controls.filter((c) => {
        const m = byId.get(c.rowId);
        return !!m && loadsProvider(m, c.provider) && loadable(c.rowId, c.provider);
    });
    // A build that answered "nothing to draw" is counted with the empty ones below, not loaded again.
    const wanted = chosen.filter((c) => !c.empty);
    const drewNothing = chosen.filter((c) => c.empty);
    const toLoad = wanted.filter((c) => !c.loaded && !c.busy);
    const loaded = wanted.filter((c) => c.loaded);
    const busy = wanted.filter((c) => c.busy).length;
    const failed = wanted.filter((c) => !c.busy && c.error);
    // Every pair the set asks for that has no geometry to load, by provider -- split into what was
    // never requested and what WAS: the provider published that very node with nothing to load
    // (an empty site, a kind it cannot draw). Asking again would publish the same nothing.
    //
    // And a third: a provider whose published tree does not name the node at all has nothing it
    // could be asked for -- a site one source models and another does not. Asking anyway fails
    // once per member ("publishes no site named ..."); they are counted, not requested.
    const missing = new Map<string, { id: string; label: string }[]>();
    const empty = new Map<string, { id: string; label: string }[]>();
    const unknown = new Map<string, { id: string; label: string }[]>();
    for (const m of present) {
        for (const p of m.providers ?? view.contentProviders) {
            if (loadable(m.id, p)) continue;
            const own = view.resolution.subjects.get(m.id)?.byProvider.get(p);
            const into = own?.manifest ? empty : view.namedBy.get(m.id)?.has(p) ? missing : unknown;
            const list = into.get(p) ?? [];
            list.push({ id: m.id, label: m.label });
            into.set(p, list);
        }
    }
    for (const c of drewNothing) {
        const list = empty.get(c.provider) ?? [];
        list.push({ id: c.rowId, label: byId.get(c.rowId)?.label ?? c.rowId });
        empty.set(c.provider, list);
    }
    const emptyCount = [...empty.values()].reduce((n, l) => n + l.length, 0);
    const unknownCount = [...unknown.values()].reduce((n, l) => n + l.length, 0);
    const missingCount = [...missing.values()].reduce((n, l) => n + l.length, 0);
    const requests = [...missing.entries()].map(([provider, rows]) => ({
        provider,
        rows,
        request: requestMissing?.(provider, rows.map((r) => r.id)) ?? null,
    }));
    const requestable = requests.filter((r) => r.request && !r.request.blocked);
    const requestableCount = requestable.reduce((n, r) => n + r.rows.length, 0);

    const loadAvailable = (op?: Operation) => void loadSelection(scope, view.collection, toLoad, op);
    const requestAndLoad = () => {
        setAsking(false);
        // ONE operation for the whole set: every provider's request, and every load, rows under it.
        const op = beginOperation(`Load set ${set.name}`);
        const release = op.hold();
        for (const r of requestable) {
            // Loaded by whichever `useAssetLoads` sees the claim first, once the publish lands.
            requestThenLoadInto(op, r.rows.map((row) => loadKey(row.id, r.provider)), (o) => r.request!.run(o));
        }
        if (toLoad.length) loadAvailable(op);
        release();
    };

    return (
        <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
                <button
                    type="button"
                    className={BTN_PRIMARY}
                    disabled={!toLoad.length && !missingCount}
                    title={toLoad.length ? toLoad.map((c) => `${rowFacts(view, c.rowId)?.node.label ?? c.rowId} · ${pn(c.provider)}`).join("\n") : "Nothing left to load"}
                    onClick={() => (missingCount ? setAsking(true) : loadAvailable())}
                >
                    {busy ? `Loading… (${busy} left)` : toLoad.length || missingCount ? `Load set (${plural(toLoad.length + missingCount, "model")})` : "Set loaded"}
                </button>
                {loaded.length > 0 && (
                    <button type="button" className={BTN_SECONDARY} onClick={() => loaded.forEach((c) => c.unload())}>
                        Unload ({loaded.length})
                    </button>
                )}
                {failed.length > 0 && (
                    <span className="text-red-300" title={failed.map((c) => `${c.rowId} · ${pn(c.provider)}: ${c.error}`).join("\n")}>
                        {failed.length} failed
                    </span>
                )}
                {missingCount > 0 && !asking && (
                    <span className="text-amber-300" title={requests.map((r) => `${pn(r.provider)}: ${r.rows.map((x) => x.label).join(", ")}`).join("\n")}>
                        {missingCount} not requested yet
                    </span>
                )}
                {emptyCount > 0 && (
                    <span
                        className="text-gray-400"
                        title={[...empty.entries()].map(([p, rows]) => `${pn(p)}: ${rows.map((x) => x.label).join(", ")}`).join("\n")}
                    >
                        {emptyCount} with nothing to draw
                    </span>
                )}
                {unknownCount > 0 && (
                    <span
                        className="text-gray-400"
                        title={[...unknown.entries()].map(([p, rows]) => `not in ${pn(p)}'s tree: ${rows.map((x) => x.label).join(", ")}`).join("\n")}
                    >
                        {unknownCount} not in that provider's tree
                    </span>
                )}
            </div>
            {asking && (
                <div role="alertdialog" aria-label="Missing geometry" className="rounded-md border border-amber-700/70 bg-amber-950/40 p-2 space-y-1.5 text-xs">
                    <div className="text-amber-100">
                        {missingCount} of {toLoad.length + missingCount} have no geometry published yet:
                    </div>
                    <ul className="space-y-0.5">
                        {requests.map((r) => (
                            <li key={r.provider} className="text-gray-200" title={`${providerIdTitle(r.provider)}\n${r.rows.map((x) => x.label).join("\n")}`}>
                                <span>{pn(r.provider)}</span>: {r.rows.length}
                                <span className="text-gray-400">
                                    {" — "}
                                    {!r.request ? "this provider cannot be asked for it" : r.request.blocked ? r.request.blocked : "can be requested"}
                                </span>
                            </li>
                        ))}
                    </ul>
                    {emptyCount > 0 && (
                        <div className="text-gray-400">
                            {emptyCount} more were requested before and have nothing to draw; they are left out.
                        </div>
                    )}
                    {unknownCount > 0 && (
                        <div className="text-gray-400" title={[...unknown.entries()].map(([p, rows]) => `${pn(p)}: ${rows.map((x) => x.label).join(", ")}`).join("\n")}>
                            {unknownCount} more are not in the chosen provider's tree at all (
                            {[...unknown.entries()].map(([p, rows]) => `${pn(p)}: ${rows.length}`).join(", ")}); nothing can be requested for them.
                        </div>
                    )}
                    <div className="flex flex-wrap gap-1.5">
                        <button
                            type="button"
                            className={BTN_PRIMARY}
                            disabled={!requestableCount}
                            title="Ask the providers for the missing geometry, load what is there now, and load each requested one as its publish arrives"
                            onClick={requestAndLoad}
                        >
                            Request {requestableCount} and load
                        </button>
                        <button
                            type="button"
                            className={BTN_SECONDARY}
                            disabled={!toLoad.length}
                            onClick={() => {
                                setAsking(false);
                                loadAvailable();
                            }}
                        >
                            Load the {toLoad.length} available
                        </button>
                        <button type="button" className={BTN_QUIET} onClick={() => setAsking(false)}>
                            Cancel
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};

export const LoadControls: React.FC<{
    view: AssetView;
    ids: readonly string[];
    scope: string;
    requests?: readonly NodeRequestControl[];
}> = ({ view, ids, scope, requests = [] }) => {
    const controls = useAssetLoads(view, ids, scope);
    const onDemand = requestLoadsFor(view, ids, controls, requests);
    const named = controls.length + onDemand.length > 1;
    const requestLoads = onDemand.map((r) => <RequestLoad key={`request:${r.provider}`} control={r} named={named} />);
    if (!controls.length && onDemand.length) {
        return <div className="flex items-center gap-2 min-w-0 flex-wrap">{requestLoads}</div>;
    }
    if (!controls.length) {
        // Drawn, and disabled: a missing button reads as a layout glitch, a greyed one as "not yet".
        return (
            <button type="button" disabled className={`${BTN_PRIMARY} shrink-0`} title={ids.length > 1 ? NOTHING_TO_LOAD_ANY : NOTHING_TO_LOAD}>
                Load into scene
            </button>
        );
    }
    if (ids.length > 1) {
        return (
            <div className="flex items-center gap-2 min-w-0 flex-wrap">
                <BulkLoads groups={loadGroups(controls)} scope={scope} collection={view.collection} />
                {requestLoads}
            </div>
        );
    }
    // One provider: the plain button. Several: one per provider, each naming it -- which
    // geometry lands in the scene is the user's choice, and the two can be compared side by side.
    return (
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
            {controls.map((control) => (
                <SingleLoad key={control.provider} control={control} named={named} />
            ))}
            {requestLoads}
        </div>
    );
};

/** "Load into scene" from a provider that has not published this node here yet: asks first, then
 *  requests, publishes and loads (`requestLoadsFor`). */
const RequestLoad: React.FC<{ control: RequestLoadControl; named: boolean }> = ({ control, named }) => {
    const name = useProviderName()(control.provider);
    return (
        <button
            type="button"
            disabled={control.busy || !!control.blocked}
            className={`${BTN_PRIMARY} shrink-0`}
            title={`${control.blocked ?? `Not published here yet — requests it from ${name} first, then loads it`}\n${providerIdTitle(control.provider)}`}
            onClick={control.run}
        >
            {control.busy ? `Requesting${named ? ` · ${name}` : ""}…` : named ? `Load · ${name}` : "Load into scene"}
        </button>
    );
};

/** A selection's loads, one set of controls per provider: load what is not in the scene yet, unload
 *  what is. Each model still loads on its own -- this only starts them together. */
const BulkLoads: React.FC<{ groups: readonly LoadGroup[]; scope: string; collection: string }> = ({ groups, scope, collection }) => {
    const pn = useProviderName();
    const named = groups.length > 1;
    return (
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
            {groups.map((g) => (
                <div key={g.provider} className="flex items-center gap-2 min-w-0">
                    <button
                        type="button"
                        disabled={!g.toLoad.length}
                        className={`${BTN_PRIMARY} shrink-0`}
                        title={`Load ${plural(g.toLoad.length, "model")} from ${pn(g.provider)}\n${providerIdTitle(g.provider)}`}
                        onClick={() => void loadSelection(scope, collection, g.toLoad)}
                    >
                        {g.toLoad.length ? `Load ${g.toLoad.length}` : "All loaded"}
                        {named ? ` · ${pn(g.provider)}` : ""}
                    </button>
                    {g.busy > 0 && <span className="text-gray-400 shrink-0">loading {g.busy}…</span>}
                    {g.loaded.length > 0 && (
                        <button
                            type="button"
                            className="text-gray-300 hover:text-white shrink-0"
                            title={`Unload the ${plural(g.loaded.length, "model")} from ${pn(g.provider)} in the scene`}
                            onClick={() => g.loaded.forEach((c) => c.unload())}
                        >
                            unload {g.loaded.length}
                        </button>
                    )}
                    {g.failed.length > 0 && (
                        <span className="text-red-300 truncate" title={g.failed.map((c) => `${c.rowId}: ${c.error}`).join("\n")}>
                            {g.failed.length} failed
                        </span>
                    )}
                </div>
            ))}
        </div>
    );
};

const SingleLoad: React.FC<{ control: AssetLoadControl; named: boolean }> = ({ control, named }) => {
    const { badge, root } = control;
    const name = useProviderName()(control.provider);
    const via = badge.weight === "ghost" ? ` via ${badge.at}` : "";
    if (control.loaded) {
        return (
            <div className="flex items-center gap-2 min-w-0" title={`${name}${via}\n${providerIdTitle(control.provider)}`}>
                <span className="text-green-300 truncate">Loaded{named ? ` · ${name}` : via ? ` (${via.trim()})` : ""}</span>
                {control.notes.length > 0 && (
                    // Said where the model is, not left for someone to find missing in it.
                    <span className="shrink-0 text-amber-300 cursor-help" title={control.notes.join("\n")}>
                        incomplete
                    </span>
                )}
                <button
                    type="button"
                    className="text-blue-300 hover:text-white disabled:text-gray-500"
                    disabled={!root}
                    title={root ? "Select this model's root in the Scene tab" : "Not in the scene yet"}
                    onClick={control.reveal}
                >
                    reveal
                </button>
                <button type="button" className="text-gray-300 hover:text-white" onClick={control.unload}>
                    unload
                </button>
            </div>
        );
    }
    return (
        <div className="flex items-center gap-2 min-w-0">
            <button
                type="button"
                disabled={control.busy}
                className={`${BTN_PRIMARY} shrink-0`}
                title={`From ${name}${via} @ ${formatRevision(badge.revision)}\n${providerIdTitle(control.provider)}`}
                onClick={() => void control.load()}
            >
                {control.busy ? "Loading…" : named ? `Load · ${name}` : "Load into scene"}
            </button>
            {control.error && (
                <span className="text-red-300 truncate" title={control.error}>
                    {control.error}
                </span>
            )}
            {control.empty && !control.error && (
                <span className="text-gray-400 truncate cursor-help" title={control.empty}>
                    nothing to draw
                </span>
            )}
        </div>
    );
};
