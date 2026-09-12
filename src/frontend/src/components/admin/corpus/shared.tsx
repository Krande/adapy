import React from "react";

// Helpers shared by the corpus tab's pieces: key manipulation, the slug rule,
// the batch size for chunked server-side moves/copies, and the flat ⇄ tree
// view toggle.

export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function dirnameOf(key: string): string {
    const i = key.lastIndexOf("/");
    return i >= 0 ? key.slice(0, i) : "";
}

export function basenameOf(key: string): string {
    return key.split("/").pop() ?? key;
}

export function normKey(key: string): string {
    return key.replace(/^\/+/, "");
}

// Batch size for chunked server-side moves/copies. Every chunk is still
// a Garage-side CopyObject (no file bytes through the browser) — the
// chunking only exists so the progress counter ticks between requests.
export const OP_CHUNK = 8;

// Flat-list ⇄ folder-tree representation switch. Storage is flat on the
// server; tree mode just groups the keys' "/" segments. Shared by the
// corpus file overview and the copy-from-scope modal.
export type ViewMode = "flat" | "tree";

export const ViewModeToggle: React.FC<{mode: ViewMode; onChange: (m: ViewMode) => void}> = ({mode, onChange}) => (
    <div className="inline-flex rounded-sm overflow-hidden border border-gray-600 shrink-0">
        {(["flat", "tree"] as const).map((m) => (
            <button
                key={m}
                type="button"
                onClick={() => onChange(m)}
                className={
                    "text-xs px-2 py-1 " +
                    (mode === m
                        ? "bg-blue-700 text-white"
                        : "bg-gray-800 text-gray-300 hover:bg-gray-700")
                }
                title={m === "flat" ? "Flat list" : "Folder tree"}
            >
                {m === "flat" ? "Flat" : "Tree"}
            </button>
        ))}
    </div>
);
