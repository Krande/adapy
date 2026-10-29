// The Systems tab: the loaded collections' systems as a tree -- kind (HVAC, Piping, ...) > system >
// run > element -- drawn like the Scene tree (same rows, glyphs and selection), from the relations
// index. Core's view of core data; a provider only publishes the relations.
//
// Selecting a row selects what it covers in the 3D view (Ctrl / Cmd adds rows, as in the Scene
// tree); right-click adds to the selection or loads the rest. A system's runs and elements are read
// when it is opened. The kinds shown are a per-viewer filter at the top.

import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {Tree, type NodeApi, type NodeRendererProps, type RowRendererProps} from "react-arborist";

import {KebabMenuItem, PositionedMenu} from "@/components/common/PositionedMenu";
import {Chevron, NodeGlyph} from "@/components/tree_view/treeGlyphs";
import {nodeRelationsApi, type SystemMember, type SystemSummary} from "@/services/api/nodeRelations";
import {useViewerStores} from "@/state/AdaViewerContext";
import {currentScope, loadedCollections, loadMembers, membersOf, selectNames, systemKindLabel} from "@/utils/systems/systems";

interface SysNode {
    id: string;
    name: string;
    level: "kind" | "system" | "run" | "element" | "wait";
    count?: number;
    system?: string;
    run?: string | null;
    children?: SysNode[];
}

const KINDS_KEY = "ada.systems.hiddenKinds";
const ROW_HEIGHT = 30;
const NO_RUN = "(not in a run)";

function readHiddenKinds(): Set<string> {
    try {
        return new Set(JSON.parse(localStorage.getItem(KINDS_KEY) ?? "[]") as string[]);
    } catch {
        return new Set();
    }
}

function writeHiddenKinds(kinds: Set<string>): void {
    try {
        localStorage.setItem(KINDS_KEY, JSON.stringify([...kinds]));
    } catch {
        /* a per-viewer convenience: without storage the filter just does not persist */
    }
}

/** A system's runs, each with its elements, from its members. */
function runNodes(system: string, members: readonly SystemMember[]): SysNode[] {
    const byRun = new Map<string, SystemMember[]>();
    for (const m of members) {
        const run = m.run ?? NO_RUN;
        byRun.set(run, [...(byRun.get(run) ?? []), m]);
    }
    return [...byRun.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([run, list]) => ({
            id: `run\u0000${system}\u0000${run}`,
            name: run,
            level: "run" as const,
            count: list.length,
            system,
            run: run === NO_RUN ? null : run,
            children: list.map((m) => ({id: `el\u0000${system}\u0000${m.full_name}`, name: m.full_name, level: "element" as const, system})),
        }));
}

/** Every element name under `node`, reading a system's members when they are not read yet. */
async function namesUnder(node: SysNode, collection: string, members: Map<string, SystemMember[]>): Promise<string[]> {
    if (node.level === "element") return [node.name];
    if ((node.level === "system" || node.level === "run") && node.system) {
        const all = members.get(node.system) ?? (await membersOf(currentScope(), collection, node.system));
        members.set(node.system, all);
        return all.filter((m) => node.level === "system" || (m.run ?? null) === (node.run ?? null)).map((m) => m.full_name);
    }
    return [];
}

const Row: React.FC<NodeRendererProps<SysNode>> = ({style, node}) => {
    const {data, isSelected, isOpen} = node;
    const branch = data.level !== "element";
    const hasChildren = branch && data.level !== "wait";
    return (
        <div
            style={style}
            className={`flex items-center gap-1.5 cursor-pointer mr-1 my-1 pr-1 rounded ${isSelected ? "bg-blue-700" : "hover:bg-white/5"}`}
            title={data.name}
        >
            <span
                className={`w-3 shrink-0 grid place-items-center ${isSelected ? "text-gray-100" : "text-gray-400"}`}
                onClick={(e) => {
                    if (!hasChildren) return;
                    e.stopPropagation();
                    node.toggle();
                }}
            >
                {hasChildren && <Chevron open={isOpen} />}
            </span>
            {data.level !== "wait" && (
                <span className={isSelected ? "text-gray-100" : "text-gray-400"}>
                    <NodeGlyph branch={branch} />
                </span>
            )}
            <span className={`whitespace-nowrap flex-1 min-w-0 truncate ${data.level === "wait" ? "text-gray-500 italic" : ""}`}>
                {data.name}
            </span>
            {data.count != null && <span className={`shrink-0 text-xs ${isSelected ? "text-gray-200" : "text-gray-500"}`}>{data.count}</span>}
        </div>
    );
};

const SystemsTab: React.FC = () => {
    const {useTreeViewStore} = useViewerStores();
    const treeData = useTreeViewStore((s) => s.treeData);
    const collections = useMemo(() => [...loadedCollections().keys()].sort(), [treeData]); // eslint-disable-line react-hooks/exhaustive-deps
    const [collection, setCollection] = useState<string | null>(null);
    const active = collection && collections.includes(collection) ? collection : (collections[0] ?? null);

    const [systems, setSystems] = useState<SystemSummary[] | null>(null);
    const [indexed, setIndexed] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(readHiddenKinds);
    const [members, setMembers] = useState<Map<string, SystemMember[]>>(new Map());
    const [message, setMessage] = useState<string | null>(null);
    const [menu, setMenu] = useState<{node: SysNode; x: number; y: number} | null>(null);
    const membersRef = useRef(members);
    membersRef.current = members;

    useEffect(() => {
        setMembers(new Map());
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

    const kinds = useMemo(() => [...new Set((systems ?? []).map((s) => systemKindLabel(s.kind)))].sort(), [systems]);

    const data = useMemo<SysNode[]>(() => {
        const byKind = new Map<string, SystemSummary[]>();
        for (const s of systems ?? []) {
            const kind = systemKindLabel(s.kind);
            if (hiddenKinds.has(kind)) continue;
            byKind.set(kind, [...(byKind.get(kind) ?? []), s]);
        }
        return [...byKind.entries()]
            .sort((a, b) => a[0].localeCompare(b[0]))
            .map(([kind, list]) => ({
                id: `kind\u0000${kind}`,
                name: kind,
                level: "kind" as const,
                count: list.length,
                children: list.map((s) => {
                    const read = members.get(s.name);
                    return {
                        id: `sys\u0000${s.name}`,
                        name: s.name,
                        level: "system" as const,
                        count: s.members,
                        system: s.name,
                        children: read ? runNodes(s.name, read) : [{id: `wait\u0000${s.name}`, name: "Reading…", level: "wait" as const}],
                    };
                }),
            }));
    }, [systems, hiddenKinds, members]);

    const readMembers = useCallback(
        (system: string) => {
            if (!active || membersRef.current.has(system)) return;
            membersOf(currentScope(), active, system)
                .then((list) => setMembers((prev) => new Map(prev).set(system, list)))
                .catch((e) => setMessage(e instanceof Error ? e.message : String(e)));
        },
        [active],
    );

    const selectRows = useCallback(
        async (nodes: SysNode[], add: boolean) => {
            if (!active || !nodes.length) return;
            const names = new Set<string>();
            const cache = new Map(membersRef.current);
            for (const n of nodes) for (const name of await namesUnder(n, active, cache)) names.add(name);
            const label = nodes.length === 1 ? nodes[0].name : `${nodes.length} rows`;
            const n = await selectNames(names, label, add);
            setMessage(n ? null : `Nothing of ${label} is loaded -- right-click and Load it first.`);
        },
        [active],
    );

    const toggleKind = (kind: string) => {
        const next = new Set(hiddenKinds);
        if (next.has(kind)) next.delete(kind);
        else next.add(kind);
        writeHiddenKinds(next);
        setHiddenKinds(next);
    };

    // Measured like the Scene tree: arborist needs a height in pixels.
    const bodyRef = useRef<HTMLDivElement>(null);
    const [height, setHeight] = useState(300);
    useEffect(() => {
        const el = bodyRef.current;
        if (!el) return;
        const ro = new ResizeObserver(() => setHeight(Math.max(120, el.clientHeight)));
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const SysRow = useMemo(
        () =>
            function SysRow({node, attrs, innerRef, children}: RowRendererProps<SysNode>) {
                return (
                    <div
                        {...attrs}
                        ref={innerRef}
                        onClick={(e) => {
                            if (node.data.level === "wait") return;
                            const add = e.ctrlKey || e.metaKey;
                            if (add) {
                                if (node.isSelected) node.deselect();
                                else node.selectMulti();
                            } else node.handleClick(e);
                        }}
                        onContextMenu={(e) => {
                            e.preventDefault();
                            if (node.data.level === "wait" || node.data.level === "kind") return;
                            setMenu({node: node.data, x: e.clientX, y: e.clientY});
                        }}
                    >
                        {children}
                    </div>
                );
            },
        [],
    );

    if (!collections.length) {
        return <p className="p-3 text-xs text-gray-400">Load a model from a published source to see its systems.</p>;
    }

    const menuItems: KebabMenuItem[] = menu
        ? [
              {
                  key: "add",
                  label: "Add to selection",
                  onClick: () => void selectRows([menu.node], true),
              },
              {
                  key: "load",
                  label: menu.node.level === "element" ? "Load element" : `Load ${menu.node.level}`,
                  title: "Each element's site into the scene, the rest of it hidden",
                  onClick: () =>
                      void (async () => {
                          if (!active) return;
                          const cache = new Map(membersRef.current);
                          const names = new Set(await namesUnder(menu.node, active, cache));
                          const all = [...cache.values()].flat().filter((m) => names.has(m.full_name));
                          setMessage(await loadMembers(active, all, menu.node.name));
                      })(),
              },
          ]
        : [];

    return (
        <div className="h-full w-full flex flex-col min-h-0 pl-1 pr-2 text-gray-100">
            <div className="w-full pr-1 pt-2 pb-1 flex flex-col gap-1.5">
                <div className="flex items-center gap-1.5">
                    {collections.length > 1 && (
                        <select
                            className="h-7 shrink-0 rounded-md border border-gray-700 bg-gray-800 px-1 text-xs text-gray-100"
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
                    )}
                    <div className="h-7 flex-1 min-w-0 flex items-center gap-2 px-2 rounded-md border border-gray-700 bg-gray-800 focus-within:border-gray-500">
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" className="text-gray-400 shrink-0">
                            <circle cx="7" cy="7" r="4.5" />
                            <path d="M10.5 10.5 14 14" strokeLinecap="round" />
                        </svg>
                        <input
                            aria-label="Search systems"
                            className="flex-1 min-w-0 bg-transparent outline-none text-[13px] text-gray-100 placeholder:text-gray-500"
                            placeholder="Search systems"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                </div>
                {kinds.length > 0 && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-300" role="group" aria-label="System kinds shown">
                        {kinds.map((kind) => (
                            <label key={kind} className="flex items-center gap-1 cursor-pointer">
                                <input type="checkbox" checked={!hiddenKinds.has(kind)} onChange={() => toggleKind(kind)} />
                                {kind}
                            </label>
                        ))}
                    </div>
                )}
                {error && <p className="text-xs text-red-400">{error}</p>}
                {!error && systems === null && <p className="text-xs text-gray-400">Reading systems…</p>}
                {!error && systems !== null && !indexed && (
                    <p className="text-xs text-gray-400">No relations are published for {active?.toUpperCase()}.</p>
                )}
                {message && <p className="text-xs text-gray-300">{message}</p>}
            </div>
            <div ref={bodyRef} className="flex-1 min-h-0 [&_*]:outline-none">
                <Tree<SysNode>
                    className="text-white scrollbar outline-none"
                    rowClassName="outline-none"
                    width="100%"
                    height={height}
                    rowHeight={ROW_HEIGHT}
                    data={data}
                    disableDrag
                    disableDrop
                    disableEdit
                    openByDefault={false}
                    searchTerm={search}
                    searchMatch={(node, term) => node.data.name.toLowerCase().includes(term.toLowerCase())}
                    renderRow={SysRow}
                    onToggle={(id) => {
                        if (id.startsWith("sys\u0000")) readMembers(id.slice(4));
                    }}
                    onSelect={(nodes: NodeApi<SysNode>[]) => {
                        const picked = nodes.map((n) => n.data).filter((d) => d.level !== "kind" && d.level !== "wait");
                        if (picked.length) void selectRows(picked, false);
                    }}
                >
                    {Row}
                </Tree>
            </div>
            {menu && <PositionedMenu items={menuItems} anchor={{kind: "point", x: menu.x, y: menu.y}} onClose={() => setMenu(null)} header={menu.node.name} />}
        </div>
    );
};

export default SystemsTab;
