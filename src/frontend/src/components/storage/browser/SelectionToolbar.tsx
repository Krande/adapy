import React from "react";
import type {ProceduralModelSummary} from "@/services/viewerApi";

// Bulk-action toolbar under the header, shown while anything is selected.
const SelectionToolbar: React.FC<{
    selection: Set<string>;
    proceduralByName: Map<string, ProceduralModelSummary>;
    bulkBusy: string | null;
    canMutate: boolean;
    onLoadSelected: () => void;
    onUnloadSelected: () => void;
    onMoveSelected: () => void;
    onDeleteSelected: () => Promise<void>;
    clearSelection: () => void;
}> = ({
    selection,
    proceduralByName,
    bulkBusy,
    canMutate,
    onLoadSelected,
    onUnloadSelected,
    onMoveSelected,
    onDeleteSelected,
    clearSelection,
}) => {
    const selectionHasVersions = Array.from(selection).some((k) =>
                    k.replace(/^\/+/, "").startsWith("versions/"),
                );
    // Models can be deleted and moved, but never loaded into the
    // scene — they are database rows. A selection of only models
    // must therefore not offer an enabled Load that quietly does
    // nothing; a mixed one says how many it will actually act on.
    const selectedModels = Array.from(selection)
                    .map((k) => proceduralByName.get(k))
                    .filter((m): m is ProceduralModelSummary => !!m);
    const selectedModelCount = selectedModels.length;
    // A model counts as loadable once it has a compiled result;
    // one that has never compiled has nothing to put in the scene.
    const loadableCount =
                    selection.size - selectedModelCount +
                    selectedModels.filter((m) => !!m.latest_glb_key).length;
    const btn = "text-white text-xs px-2 py-1 rounded-sm min-h-[36px] sm:min-h-0 cursor-pointer disabled:opacity-60 disabled:cursor-default";
    return (
                    <div className="mb-2 px-2 py-1.5 rounded-sm border border-gray-700 bg-gray-800/95 flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-white whitespace-nowrap">
                            {selection.size} selected
                            {selectedModelCount > 0 && (
                                <span className="text-gray-400">
                                    {" "}({selectedModelCount} model{selectedModelCount === 1 ? "" : "s"})
                                </span>
                            )}
                        </span>
                        <button
                            type="button"
                            onClick={onLoadSelected}
                            disabled={bulkBusy !== null || loadableCount === 0}
                            title={
                                loadableCount === 0
                                    ? "Nothing here can be shown — a procedural model needs one compile first."
                                    : selectedModelCount > 0
                                      ? `Loads ${loadableCount} of ${selection.size} selected; the last model ticked becomes active.`
                                      : undefined
                            }
                            className={`bg-blue-700 hover:bg-blue-600 active:bg-blue-800 ${btn}`}
                        >
                            Load
                        </button>
                        <button
                            type="button"
                            onClick={onUnloadSelected}
                            disabled={bulkBusy !== null}
                            className={`bg-gray-700 hover:bg-gray-600 active:bg-gray-800 ${btn}`}
                        >
                            {bulkBusy === "unload" ? "Unloading…" : "Unload"}
                        </button>
                        {canMutate && (
                            <button
                                type="button"
                                onClick={onMoveSelected}
                                disabled={bulkBusy !== null || selectionHasVersions}
                                title={selectionHasVersions ? "CI version files can't be moved" : "Move selected files to a folder"}
                                className={`bg-gray-700 hover:bg-gray-600 active:bg-gray-800 ${btn}`}
                            >
                                Move…
                            </button>
                        )}
                        {canMutate && (
                            <button
                                type="button"
                                onClick={() => void onDeleteSelected()}
                                disabled={bulkBusy !== null || selectionHasVersions}
                                title={selectionHasVersions ? "CI version files can't be deleted" : "Delete selected files (incl. converted caches)"}
                                className={`bg-red-800 hover:bg-red-700 active:bg-red-900 ${btn}`}
                            >
                                {bulkBusy === "delete" ? "Deleting…" : "Delete"}
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={clearSelection}
                            disabled={bulkBusy !== null}
                            className={`ml-auto bg-gray-600 hover:bg-gray-500 ${btn}`}
                        >
                            Cancel
                        </button>
                    </div>
    );
};

export default SelectionToolbar;
