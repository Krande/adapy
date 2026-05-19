// Modal for picking a destination folder. Replaces the
// ``window.prompt`` paths in the storage panels' Move-to-folder
// flows. Offers two paths in one UI:
//
//   * Existing folders: a dropdown populated with every folder
//     path currently in the scope (derived from the file list by
//     the caller).
//   * New folder: a text input, used when none of the existing
//     folders fit or the user wants to create a new prefix.
//
// Mode is a radio toggle; the disabled side stays visible so the
// user can flip without re-reading the modal.
//
// Portaled to ``document.body`` so the backdrop covers the whole
// viewport regardless of where the trigger row lives — same
// reason the kebab menu uses a portal.

import React, {useEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";

export interface FolderPickerModalProps {
    open: boolean;
    title: string;
    /** Sorted, deduped folder paths to offer in the dropdown. Empty
     *  list is fine — the modal falls back to the new-folder input
     *  only. */
    existingFolders: string[];
    /** Pre-fill for the new-folder input. Used by the "Move folder
     *  into…" flow to suggest the source folder's basename. */
    initialNew?: string;
    onCancel: () => void;
    onPick: (folder: string) => void;
    /** Button label — defaults to "Move". Folder-rename / move-into
     *  flows pass a more specific verb. */
    submitLabel?: string;
}

const FolderPickerModal: React.FC<FolderPickerModalProps> = ({
    open, title, existingFolders, initialNew, onCancel, onPick, submitLabel,
}) => {
    const hasExisting = existingFolders.length > 0;
    const [mode, setMode] = useState<"existing" | "new">(hasExisting ? "existing" : "new");
    const [selected, setSelected] = useState<string>(existingFolders[0] ?? "");
    const [newPath, setNewPath] = useState<string>(initialNew ?? "");
    const newInputRef = useRef<HTMLInputElement>(null);

    // Re-seed defaults each time the modal opens — without this, a
    // user who cancels and re-opens against a different selection
    // would see stale state.
    useEffect(() => {
        if (!open) return;
        setMode(hasExisting ? "existing" : "new");
        setSelected(existingFolders[0] ?? "");
        setNewPath(initialNew ?? "");
    }, [open, hasExisting, existingFolders, initialNew]);

    // Focus the new-folder input the moment the user switches to
    // that mode, mirroring the prompt() ergonomics.
    useEffect(() => {
        if (mode === "new" && open) {
            newInputRef.current?.focus();
        }
    }, [mode, open]);

    if (!open) return null;

    const submit = () => {
        const value = mode === "existing" ? selected : newPath;
        const trimmed = value.trim().replace(/^\/+|\/+$/g, "");
        if (!trimmed) return;
        onPick(trimmed);
    };

    return createPortal(
        <div
            className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 sm:p-8 overflow-y-auto"
            onMouseDown={(e) => {
                // Click on backdrop dismisses. Stop propagation
                // inside the panel itself so a stray mousedown there
                // doesn't dismiss the user mid-edit.
                if (e.target === e.currentTarget) onCancel();
            }}
            role="dialog"
            aria-modal="true"
            aria-label={title}
        >
            <div className="w-full max-w-md rounded-sm border border-gray-700 bg-gray-800 text-gray-100 shadow-xl">
                <div className="px-4 py-3 border-b border-gray-700 text-sm font-semibold">
                    {title}
                </div>
                <div className="px-4 py-3 space-y-3 text-sm">
                    {hasExisting && (
                        <label className="flex items-start gap-2 cursor-pointer">
                            <input
                                type="radio"
                                checked={mode === "existing"}
                                onChange={() => setMode("existing")}
                                className="mt-1 shrink-0"
                            />
                            <div className="flex-1 min-w-0">
                                <div className="text-gray-200 mb-1">
                                    Existing folder
                                </div>
                                <select
                                    value={selected}
                                    onChange={(e) => {
                                        setSelected(e.target.value);
                                        setMode("existing");
                                    }}
                                    className="w-full bg-gray-700 border border-gray-600 rounded-sm px-2 py-1 font-mono text-xs"
                                >
                                    {existingFolders.map((p) => (
                                        <option key={p} value={p}>{p}</option>
                                    ))}
                                </select>
                            </div>
                        </label>
                    )}
                    <label className="flex items-start gap-2 cursor-pointer">
                        <input
                            type="radio"
                            checked={mode === "new"}
                            onChange={() => setMode("new")}
                            className="mt-1 shrink-0"
                        />
                        <div className="flex-1 min-w-0">
                            <div className="text-gray-200 mb-1">
                                {hasExisting ? "New folder" : "Folder path"}
                            </div>
                            <input
                                ref={newInputRef}
                                type="text"
                                value={newPath}
                                onChange={(e) => {
                                    setNewPath(e.target.value);
                                    setMode("new");
                                }}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") submit();
                                    if (e.key === "Escape") onCancel();
                                }}
                                placeholder="e.g. projects/alpha/inputs"
                                className="w-full bg-gray-700 border border-gray-600 rounded-sm px-2 py-1 font-mono text-xs"
                            />
                        </div>
                    </label>
                </div>
                <div className="px-4 py-3 border-t border-gray-700 flex items-center justify-end gap-2">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="px-3 py-1 rounded-sm bg-gray-700 hover:bg-gray-600 text-xs"
                    >
                        Cancel
                    </button>
                    <button
                        type="button"
                        onClick={submit}
                        className="px-3 py-1 rounded-sm bg-blue-700 hover:bg-blue-600 text-xs disabled:opacity-50"
                        disabled={
                            (mode === "existing" && !selected.trim()) ||
                            (mode === "new" && !newPath.trim())
                        }
                    >
                        {submitLabel ?? "Move"}
                    </button>
                </div>
            </div>
        </div>,
        document.body,
    );
};

export default FolderPickerModal;
