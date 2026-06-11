import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from "react-dom";
import {useVirtualizer} from "@tanstack/react-virtual";

import type {GroupInfo} from '@/state/sceneInfoStore';
import {useViewerStores, useViewerRefs} from '@/state/AdaViewerContext';
import {selectGroupMembers} from "@/utils/selectGroupMembers";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";


const GroupsSection = () => {
    const {useSceneInfoStore, useObjectInfoStore} = useViewerStores();
    const {adaExtension: adaExtensionRef, scene: sceneRef} = useViewerRefs();
    const {
        selectedGroup,
        availableGroups,
        setSelectedGroup,
        setAvailableGroups,
    } = useSceneInfoStore();

    // Collect groups from ADA extension on component mount. Some
    // fixtures (e.g. ship1t1.fem via the legacy convert path) bake
    // thousands of element / node sets into ADA_EXT_data — building
    // the array is cheap, but the previous render path expanded the
    // whole list as <option> children of a native <select>, which
    // froze the main thread on mount. The combobox below virtualizes
    // the list so the render stays bounded.
    useEffect(() => {
        const collectGroups = () => {
            const groups: GroupInfo[] = [];
            const adaExtension = adaExtensionRef.current;

            if (adaExtension) {
                if (adaExtension.design_objects) {
                    adaExtension.design_objects.forEach(designObj => {
                        if (designObj.groups) {
                            designObj.groups.forEach(group => {
                                groups.push({
                                    name: group.name || 'Unnamed Group',
                                    description: group.description,
                                    members: group.members,
                                    type: 'design' as const,
                                    parent_name: designObj.name || 'Unnamed Object'
                                });
                            });
                        }
                    });
                }

                if (adaExtension.simulation_objects) {
                    adaExtension.simulation_objects.forEach(simObj => {
                        if (simObj.groups) {
                            simObj.groups.forEach(group => {
                                groups.push({
                                    name: group.name || 'Unnamed Group',
                                    description: group.description,
                                    members: group.members,
                                    type: 'simulation' as const,
                                    parent_name: simObj.name || 'Unnamed Object',
                                    fe_object_type: group.fe_object_type
                                });
                            });
                        }
                    });
                }
            }

            // Only overwrite when the ADA extension actually yielded groups. Streaming FEM/FEA
            // models carry no ADA_EXT — their groups are pushed straight into this store by
            // load_fea_streaming — so an empty collection here must NOT clobber them (the panel
            // can mount after the model loads). Unload clears the store explicitly.
            if (groups.length > 0) setAvailableGroups(groups);
        };

        collectGroups();
    }, [setAvailableGroups]);

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

            const customBatchedMeshes: CustomBatchedMesh[] = [];
            sceneRef.current?.traverse(obj => {
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
    const [query, setQuery] = useState("");
    const triggerRef = useRef<HTMLButtonElement | null>(null);
    const popoverRef = useRef<HTMLDivElement | null>(null);
    const listRef = useRef<HTMLDivElement | null>(null);
    const searchRef = useRef<HTMLInputElement | null>(null);

    const filteredGroups = useMemo(() => {
        if (!query) return groups;
        const q = query.toLowerCase();
        return groups.filter((g) => g.name.toLowerCase().includes(q));
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
                                    const isSelected = selected?.name === g.name;
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
                                            title={`${g.name} (${g.type})`}
                                        >
                                            {g.name}{" "}
                                            <span className="text-gray-400">({g.type})</span>
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
