// The Systems tab: every system the loaded collections' relations name -- HVAC, piping, whatever a
// provider publishes `serves_system` for -- with its size, to select what of it is loaded or load the
// rest. Core's view of core data; a provider only publishes the relations.

import React, {useEffect, useMemo, useState} from "react";

import {nodeRelationsApi, type SystemSummary} from "@/services/api/nodeRelations";
import {useViewerStores} from "@/state/AdaViewerContext";
import {currentScope, loadedCollections, loadMembers, membersOf, selectNames, systemKindLabel} from "@/utils/systems/systems";

const SHOWN_MAX = 300;

const SystemsTab: React.FC = () => {
    const {useTreeViewStore} = useViewerStores();
    const treeData = useTreeViewStore((s) => s.treeData);
    const collections = useMemo(() => [...loadedCollections().keys()].sort(), [treeData]); // eslint-disable-line react-hooks/exhaustive-deps
    const [collection, setCollection] = useState<string | null>(null);
    const [systems, setSystems] = useState<SystemSummary[] | null>(null);
    const [indexed, setIndexed] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [filter, setFilter] = useState("");
    const [message, setMessage] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);

    const active = collection && collections.includes(collection) ? collection : (collections[0] ?? null);

    useEffect(() => {
        if (!active) {
            setSystems(null);
            return;
        }
        let live = true;
        setSystems(null);
        setError(null);
        nodeRelationsApi
            .systems(currentScope(), active)
            .then((r) => {
                if (!live) return;
                setIndexed(r.indexed);
                setSystems(r.systems);
            })
            .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
        return () => {
            live = false;
        };
    }, [active]);

    const groups = useMemo(() => {
        const needle = filter.trim().toLowerCase();
        const byKind = new Map<string, SystemSummary[]>();
        for (const s of systems ?? []) {
            if (needle && !s.name.toLowerCase().includes(needle)) continue;
            const kind = systemKindLabel(s.kind);
            byKind.set(kind, [...(byKind.get(kind) ?? []), s]);
        }
        return [...byKind.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    }, [systems, filter]);

    const act = async (s: SystemSummary, what: "select" | "load") => {
        if (!active) return;
        setBusy(`${what}:${s.name}`);
        setMessage(null);
        try {
            const members = await membersOf(currentScope(), active, s.name);
            if (what === "select") {
                const n = await selectNames(members.map((m) => m.full_name), s.name);
                setMessage(n ? `Selected ${s.name}: ${n} drawn element${n === 1 ? "" : "s"}.` : `Nothing of ${s.name} is loaded -- Load it first.`);
            } else {
                setMessage(await loadMembers(active, members, s.name));
            }
        } catch (e) {
            setMessage(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
        }
    };

    if (!collections.length) {
        return <p className="p-3 text-xs text-gray-400">Load a model from a published source to see its systems.</p>;
    }
    return (
        <div className="flex flex-col gap-2 p-2 text-sm min-h-0 flex-1">
            <div className="flex items-center gap-2">
                {collections.length > 1 ? (
                    <select
                        className="bg-gray-800 border border-white/15 rounded px-1 py-0.5 text-xs"
                        value={active ?? ""}
                        onChange={(e) => setCollection(e.target.value)}
                        aria-label="Collection"
                    >
                        {collections.map((c) => (
                            <option key={c} value={c}>
                                {c.toUpperCase()}
                            </option>
                        ))}
                    </select>
                ) : (
                    <span className="text-xs font-semibold">{active?.toUpperCase()}</span>
                )}
                <input
                    className="flex-1 min-w-0 bg-gray-800 border border-white/15 rounded px-1.5 py-0.5 text-xs"
                    placeholder="Filter systems"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                />
            </div>
            {error && <p className="text-xs text-red-400">{error}</p>}
            {!error && systems === null && <p className="text-xs text-gray-400">Reading systems…</p>}
            {!error && systems !== null && !indexed && (
                <p className="text-xs text-gray-400">No relations are published for {active?.toUpperCase()}.</p>
            )}
            {message && <p className="text-xs text-gray-300">{message}</p>}
            <div className="min-h-0 flex-1 overflow-auto flex flex-col gap-2">
                {groups.map(([kind, list]) => (
                    <section key={kind}>
                        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                            {kind} <span className="font-normal">({list.length})</span>
                        </h3>
                        <ul className="flex flex-col">
                            {list.slice(0, SHOWN_MAX).map((s) => (
                                <li key={`${s.provider}:${s.name}`} className="flex items-center gap-1.5 py-0.5 text-xs">
                                    <button
                                        type="button"
                                        className="flex-1 min-w-0 truncate text-left hover:underline"
                                        title={`${s.name}\n${s.members} element(s) in ${s.runs} run(s)${s.system_type ? ` · ${s.system_type}` : ""}\nClick to select what of it is loaded`}
                                        onClick={() => void act(s, "select")}
                                        disabled={busy !== null}
                                    >
                                        {s.name}
                                    </button>
                                    <span className="text-gray-400 shrink-0">{s.members}</span>
                                    <button
                                        type="button"
                                        className="shrink-0 px-1.5 rounded border border-white/20 hover:bg-white/10 disabled:opacity-50"
                                        onClick={() => void act(s, "load")}
                                        disabled={busy !== null}
                                        title={`Load ${s.name}: each element's site, the rest of it hidden`}
                                    >
                                        {busy === `load:${s.name}` ? "…" : "Load"}
                                    </button>
                                </li>
                            ))}
                            {list.length > SHOWN_MAX && (
                                <li className="text-xs text-gray-400">and {list.length - SHOWN_MAX} more -- filter to find them</li>
                            )}
                        </ul>
                    </section>
                ))}
            </div>
        </div>
    );
};

export default SystemsTab;
