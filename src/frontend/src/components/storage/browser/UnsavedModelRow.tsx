import React from "react";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {confirmDialog} from "@/plugins/dialogs";
import ProceduralModelIcon from "../../icons/ProceduralModelIcon";

// The open procedural model when the scope's saved list does not hold it: it exists only
// in this tab, so without this row the panel offers no handle on it, not even to close it.
const UnsavedModelRow: React.FC<{name: string}> = ({name}) => {
    const close = async () => {
        if (!await confirmDialog({
            title: "Close without saving?",
            body: [`"${name}" is not saved.`, "Closing discards it; there is no copy on the server to reopen."],
            confirmLabel: "Discard",
            tone: "danger",
        })) return;
        useCellBuilderStore.getState().close();
    };
    return (
        <div
            className="mb-1 flex items-center gap-1.5 px-2 py-1 rounded-sm bg-blue-900/40"
            title="Open in the cellbuilder and not saved: it exists only in this tab"
        >
            <input
                type="checkbox"
                className="h-5 w-5 shrink-0 cursor-pointer"
                checked
                aria-label={`Close ${name}`}
                title="Open in the cellbuilder; untick to close it"
                onChange={() => void close()}
            />
            <ProceduralModelIcon className="shrink-0"/>
            <span className="truncate text-sm">{name}</span>
            <span className="text-[10px] text-amber-400 border border-amber-400/50 rounded-sm px-1">
                not saved
            </span>
        </div>
    );
};

/** The open model's name while it is not in the saved list, else null. */
export function unsavedModelName(
    active: {modelId: string; name: string} | null,
    saved: readonly {id: string}[],
): string | null {
    if (!active) return null;
    return saved.some((m) => m.id === active.modelId) ? null : active.name;
}

export default UnsavedModelRow;
