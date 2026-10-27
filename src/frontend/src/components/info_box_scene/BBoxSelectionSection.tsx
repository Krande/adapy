import React from "react";

import {useBBoxSelectionStore} from "@/state/bboxSelectionStore";
import {addBoxAroundSelection, applyBoxSelection} from "@/utils/bboxSelection/applyBoxSelection";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {BoxRequestStatus} from "@/components/viewer/BoxRequestSection";

// The Scene panel's list of selection boxes: show or hide each, make one the active box
// (gizmo, handles, selection), or delete it. Boxes are kept per scope across reloads.

const BBoxSelectionSection: React.FC = () => {
    const boxes = useBBoxSelectionStore((s) => s.boxes);
    const activeId = useBBoxSelectionStore((s) => s.activeId);
    const {update, remove, setActive, clearAll} = useBBoxSelectionStore.getState();
    const hasSelection = useSelectedObjectStore((s) => s.selectedObjects.size > 0);

    return (
        <div className="space-y-1 p-1 text-xs">
            <div className="flex items-center gap-1.5">
                <button
                    type="button"
                    className="rounded-sm bg-blue-600 px-2 py-1 text-white disabled:opacity-50"
                    disabled={!hasSelection}
                    title={hasSelection ? "A box around the current selection" : "Select something first, or right-click an element in the 3D view"}
                    onClick={() => addBoxAroundSelection()}
                >
                    + Box around selection
                </button>
                {boxes.length > 0 && (
                    <button
                        type="button"
                        className="ml-auto rounded-sm px-2 py-1 text-red-300 hover:bg-gray-500/40"
                        title="Delete every selection box, here and in this browser's saved copy"
                        onClick={() => clearAll()}
                    >
                        Clear all
                    </button>
                )}
            </div>
            {boxes.length === 0 && (
                <p className="italic text-gray-400">No selection boxes. Right-click an element and choose "Select with bounding box".</p>
            )}
            {boxes.map((b) => (
                <div key={b.id}>
                    <div className="flex items-center gap-1.5">
                        <input
                            type="checkbox"
                            checked={b.visible}
                            onChange={(e) => update(b.id, {visible: e.target.checked})}
                            title={b.visible ? "Hide the box" : "Show the box"}
                        />
                        <span className="inline-block h-3 w-3 rounded-sm border border-gray-500" style={{background: b.color}}/>
                        <label className="flex flex-1 cursor-pointer items-center gap-1 truncate" title="Make this the active box">
                            <input
                                type="radio"
                                name="bbox-active"
                                checked={activeId === b.id}
                                onChange={() => {
                                    setActive(b.id);
                                    applyBoxSelection(b.id);
                                }}
                            />
                            <span className={b.visible ? "" : "opacity-50"}>{b.label}</span>
                        </label>
                        <span className="text-gray-400">{b.mode === "full" ? "fully" : "partially"}</span>
                        <button
                            type="button"
                            className="rounded-sm px-1 text-red-300 hover:bg-gray-500/40"
                            onClick={() => remove(b.id)}
                            aria-label={`Delete ${b.label}`}
                            title="Delete"
                        >
                            ✕
                        </button>
                    </div>
                    <div className="pl-5">
                        <BoxRequestStatus boxId={b.id}/>
                    </div>
                </div>
            ))}
        </div>
    );
};

export default BBoxSelectionSection;
