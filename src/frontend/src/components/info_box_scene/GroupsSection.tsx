import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from "react-dom";
import {useVirtualizer} from "@tanstack/react-virtual";

import type {GroupInfo} from '@/state/sceneInfoStore';
import {useViewerStores, useViewerRefs} from '@/state/AdaViewerContext';
import {useModelState, loadedSourceGroups} from '@/state/modelState';
import {selectGroupMembers} from "@/utils/selectGroupMembers";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

// Pull every group out of one model's ADA extension, tagged with the
// storage key it came from.
function groupsFromExtension(ext: any, source: string | undefined): GroupInfo[] {
    const groups: GroupInfo[] = [];
    for (const designObj of ext?.design_objects ?? []) {
        for (const group of designObj.groups ?? []) {
            groups.push({
                name: group.name || 'Unnamed Group',
                description: group.description,
                members: group.members,
                type: 'design' as const,
                parent_name: designObj.name || 'Unnamed Object',
                source,
            });
        }
    }
    for (const simObj of ext?.simulation_objects ?? []) {
        for (const group of simObj.groups ?? []) {
            groups.push({
                name: group.name || 'Unnamed Group',
                description: group.description,
                members: group.members,
                type: 'simulation' as const,
                parent_name: simObj.name || 'Unnamed Object',
                fe_object_type: group.fe_object_type,
                source,
            });
        }
    }
    return groups;
}

const GroupsSection = () => {
    const {useSceneInfoStore, useObjectInfoStore} = useViewerStores();
    const {adaExtension: adaExtensionRef, scene: sceneRef} = useViewerRefs();
    const loadedSourceNames = useModelState((s) => s.loadedSourceNames);
    const {
        selectedGroup,
        availableGroups,
        setSelectedGroup,
        setAvailableGroups,
    } = useSceneInfoStore();

    // Collect groups from EVERY loaded model's ADA extension — each
    // scene group keeps its own copy in userData.__adaExt, so multi-
    // model overlays contribute one batch per source file (the old
    // single adaExtensionRef only ever held the LAST loaded model).
    // Subscribing to loadedSourceNames keeps the list live across
    // load/unload instead of snapshotting on mount. Some fixtures
    // (e.g. a large ship FEM via the legacy convert path) bake thousands
    // of element / node sets into ADA_EXT_data — building the array
    // is cheap; the combobox below virtualizes the render.
    useEffect(() => {
        // Scene emptied → drop everything, selection included. The
        // active-extension ref still holds the LAST model's data at
        // this point, so it must not be consulted.
        if (loadedSourceNames.size === 0) {
            setAvailableGroups([]);
            setSelectedGroup(null);
            return;
        }

        const groups: GroupInfo[] = [];
        for (const name of loadedSourceNames) {
            const sceneGroup = loadedSourceGroups.get(name);
            if (!sceneGroup) continue;
            const ext = (sceneGroup.children?.[0] as any)?.userData?.__adaExt
                ?? (sceneGroup as any)?.userData?.__adaExt;
            if (ext) groups.push(...groupsFromExtension(ext, name));
        }
        // Fallback for the streaming/replace path (no per-source scene
        // groups registered): the single active-extension ref.
        if (groups.length === 0 && adaExtensionRef.current) {
            groups.push(...groupsFromExtension(adaExtensionRef.current, undefined));
        }

        // Only overwrite when collection actually yielded groups.
        // Streaming FEM/FEA models carry no ADA_EXT — their groups are
        // pushed straight into this store by load_fea_streaming — so an
        // empty collection must NOT clobber them.
        if (groups.length > 0) setAvailableGroups(groups);

        // A selected group whose model just unloaded is gone — clear it
        // so the details table doesn't describe a ghost.
        const sel = useSceneInfoStore.getState?.()
            ? useSceneInfoStore.getState().selectedGroup
            : null;
        if (sel?.source && !loadedSourceNames.has(sel.source)) {
            setSelectedGroup(null);
        }
    }, [setAvailableGroups, setSelectedGroup, useSceneInfoStore, loadedSourceNames, adaExtensionRef]);

    const applyGroupSelection = async (group: GroupInfo | null) => {
        if (!group) {
            setSelectedGroup(null);
            useObjectInfoStore.getState().setName('');
            await selectGroupMembers("", [], undefined);
            return;
        }

        setSelectedGroup(group);

        if (group.members && group.members.length > 0) {
            useObjectInfoStore.getState().setName(`Group: ${group.name}`);

            // Resolve parent_name → mesh inside the owning model's scene
            // group when we know the source; two loaded files with
            // same-named objects would otherwise race on whichever the
            // whole-scene traversal finds first.
            const searchRoot = (group.source && loadedSourceGroups.get(group.source))
                || sceneRef.current;
            const customBatchedMeshes: CustomBatchedMesh[] = [];
            searchRoot?.traverse(obj => {
                if (obj instanceof CustomBatchedMesh) {
                    customBatchedMeshes.push(obj);
                }
            });
            let mesh_obj: CustomBatchedMesh | null = null;
            for (const cbm of customBatchedMeshes) {
                if (cbm.ada_ext_data?.name == group.parent_name) {
                    mesh_obj = cbm;
                    break;
                }
            }

            if (!mesh_obj) {
                console.warn(`Parent object ${group.parent_name} not found in scene`);
                return;
            }
            await selectGroupMembers(mesh_obj.unique_key, group.members, group.fe_object_type);
        }
    };

    return (
        <div className="table">
            <div className="table-row">
                <div className="table-cell w-24 align-top pt-1">Group:</div>
                <div className="table-cell w-48">
                    <GroupCombobox
                        groups={availableGroups}
                        selected={selectedGroup}
                        onSelect={(g) => void applyGroupSelection(g)}
                    />
                </div>
            </div>

            {selectedGroup && (
                <>
                    {selectedGroup.source && (
                        <div className="table-row">
                            <div className="table-cell w-24">Model:</div>
                            <div className="table-cell w-48 truncate" title={selectedGroup.source}>
                                {selectedGroup.source.split("/").pop()}
                            </div>
                        </div>
                    )}
                    <div className="table-row">
                        <div className="table-cell w-24">Type:</div>
                        <div className="table-cell w-48 capitalize">
                            {selectedGroup.type} Object
                        </div>
                    </div>

                    <div className="table-row">
                        <div className="table-cell w-24">Description:</div>
                        <div className="table-cell w-48">
                            {selectedGroup.description || 'No description available'}
                        </div>
                    </div>

                    <div className="table-row">
                        <div className="table-cell w-24">Members:</div>
                        <div className="table-cell w-48">
                            {selectedGroup.members ? selectedGroup.members.length : 0}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};

// ── Combobox ─────────────────────────────────────────────────────────
//
// Native <select> can't be virtualized — its <option> children must be
// in the DOM. For multi-thousand-group fixtures that's a few seconds of
// main-thread freeze on mount. This trigger-button-plus-popover
// rendering pattern lets us:
//
//   * mount nothing until the user opens it (the trigger only stores
//     the current selection + group count);
//   * window-render the option list via @tanstack/react-virtual so
//     only the ~10 visible rows hit the DOM;
//   * add a search filter, which is the real ergonomics fix — scrolling
//     5k groups to find one is unworkable even if the list is fast.
//
// Keyboard support is deliberately minimal (Esc closes). Adding full
// arrow-key navigation is straightforward but felt out of scope for
// the freeze fix.

const ROW_HEIGHT_PX = 28;

interface GroupComboboxProps {
    groups: GroupInfo[];
    selected: GroupInfo | null;
    onSelect: (group: GroupInfo | null) => void;
}

const GroupCombobox: React.FC<GroupComboboxProps> = ({groups, selected, onSelect}) => {
    const [open, setOpen] = useState(false);
    const multiSource = useMemo(
        () => new Set(groups.map((g) => g.source ?? "")).size > 1,
        [groups],
    );
    const [query, setQuery] = useState("");
    const triggerRef = useRef<HTMLButtonElement | null>(null);
    const popoverRef = useRef<HTMLDivElement | null>(null);
    const listRef = useRef<HTMLDivElement | null>(null);
    const searchRef = useRef<HTMLInputElement | null>(null);

    const filteredGroups = useMemo(() => {
        if (!query) return groups;
        const q = query.toLowerCase();
        return groups.filter((g) =>
            g.name.toLowerCase().includes(q) ||
            (g.source ?? "").toLowerCase().includes(q));
    }, [groups, query]);

    const rowVirtualizer = useVirtualizer({
        count: filteredGroups.length,
        getScrollElement: () => listRef.current,
        estimateSize: () => ROW_HEIGHT_PX,
        overscan: 8,
    });

    // Close on outside click + Esc.
    useEffect(() => {
        if (!open) return;
        const onMouseDown = (e: MouseEvent) => {
            const t = e.target as Node;
            if (triggerRef.current?.contains(t)) return;
            if (popoverRef.current?.contains(t)) return;
            setOpen(false);
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") setOpen(false);
        };
        document.addEventListener("mousedown", onMouseDown);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", onMouseDown);
            document.removeEventListener("keydown", onKey);
        };
    }, [open]);

    // Autofocus the filter when the popover opens — typing is the
    // primary navigation for long lists.
    useEffect(() => {
        if (open) searchRef.current?.focus();
    }, [open]);

    // Portal placement: fixed, anchored under the trigger, floating
    // over the 3D view. Rendering the popover inline made it part of
    // the Scene panel's overflow-y-auto scroll area — opening it grew
    // the panel and on mobile shifted the whole page. The list height
    // clamps to the space below the trigger so it never runs off the
    // bottom edge.
    const [pos, setPos] = useState<{top: number; left: number; width: number; maxH: number} | null>(null);
    useLayoutEffect(() => {
        if (!open) {
            setPos(null);
            return;
        }
        const place = () => {
            const rect = triggerRef.current?.getBoundingClientRect();
            if (!rect) return;
            const width = Math.max(rect.width, 220);
            const left = Math.min(rect.left, window.innerWidth - width - 8);
            const maxH = Math.max(120, Math.min(240, window.innerHeight - rect.bottom - 48));
            setPos({top: rect.bottom + 4, left: Math.max(8, left), width, maxH});
        };
        place();
        window.addEventListener("resize", place);
        // capture:true so the popover tracks the panel scrolling under it.
        window.addEventListener("scroll", place, true);
        return () => {
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [open]);

    const triggerLabel = selected
        ? `${selected.name} (${selected.type})`
        : groups.length === 0
            ? "No groups available"
            : `Select a group… (${groups.length})`;

    return (
        <div className="relative">
            <div className="flex">
                <button
                    ref={triggerRef}
                    type="button"
                    onClick={() => setOpen((v) => !v)}
                    className="flex-1 p-1 rounded-sm bg-gray-700 border border-gray-600 text-gray-100 text-left text-xs truncate"
                    disabled={groups.length === 0}
                    title={triggerLabel}
                >
                    {triggerLabel}
                </button>
                {selected && (
                    <button
                        type="button"
                        onClick={() => onSelect(null)}
                        className="ml-1 px-1.5 bg-gray-700 border border-gray-600 rounded-sm text-xs text-gray-300 hover:bg-gray-600"
                        title="Clear selection"
                        aria-label="Clear selection"
                    >
                        ×
                    </button>
                )}
            </div>
            {open && pos && createPortal(
                <div
                    ref={popoverRef}
                    className="fixed z-50 bg-gray-800 border border-gray-600 text-gray-100 rounded-sm shadow-lg"
                    style={{top: pos.top, left: pos.left, width: pos.width}}
                >
                    <input
                        ref={searchRef}
                        type="text"
                        // text-base on touch: iOS zooms the page when a
                        // focused input's font-size is under 16px, which
                        // is the "whole page shifts" effect on mobile.
                        className="w-full p-1 bg-gray-800 text-gray-100 placeholder-gray-400 border-b border-gray-600 text-base sm:text-xs"
                        placeholder={`Filter ${groups.length} group${groups.length === 1 ? "" : "s"}…`}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                    />
                    <div
                        ref={listRef}
                        className="overflow-auto"
                        style={{maxHeight: pos.maxH}}
                    >
                        {filteredGroups.length === 0 ? (
                            <div className="px-2 py-2 text-xs text-gray-400 italic">
                                No matches
                            </div>
                        ) : (
                            <div
                                style={{
                                    height: rowVirtualizer.getTotalSize(),
                                    position: "relative",
                                    width: "100%",
                                }}
                            >
                                {rowVirtualizer.getVirtualItems().map((vRow) => {
                                    const g = filteredGroups[vRow.index];
                                    const isSelected = selected?.name === g.name && selected?.source === g.source;
                                    return (
                                        <button
                                            key={`${g.name}-${vRow.index}`}
                                            type="button"
                                            onClick={() => {
                                                onSelect(g);
                                                setOpen(false);
                                                setQuery("");
                                            }}
                                            className={
                                                "absolute left-0 right-0 px-2 text-left text-xs truncate " +
                                                (isSelected
                                                    ? "bg-blue-900/60"
                                                    : "hover:bg-gray-700")
                                            }
                                            style={{
                                                top: vRow.start,
                                                height: vRow.size,
                                                lineHeight: `${vRow.size}px`,
                                            }}
                                            title={g.source ? `${g.name} (${g.type}) — ${g.source}` : `${g.name} (${g.type})`}
                                        >
                                            {g.name}{" "}
                                            <span className="text-gray-400">({g.type})</span>
                                            {multiSource && g.source && (
                                                <span className="text-gray-400"> · {g.source.split("/").pop()}</span>
                                            )}
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>,
                document.body,
            )}
        </div>
    );
};

export default GroupsSection;
