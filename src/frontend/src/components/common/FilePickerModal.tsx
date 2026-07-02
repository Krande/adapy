// Single-file picker over a scope's S3 storage. Reuses the shared FileTreeView (the same folder-tree
// row look as the main StorageBrowser) so picking a compare file uses the storage-browser UI rather
// than a bespoke text field. Read-only: it lists files and returns the chosen key — uploads stay in
// the normal storage browser flow. Portaled to document.body like the other picker modals.

import React from "react";
import {createPortal} from "react-dom";

import {viewerApi, type FileEntry, type ScopeUrl} from "@/services/viewerApi";
import {buildFileTree} from "@/utils/storage/fileTree";
import FileTreeView from "@/components/admin/FileTreeView";

function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    const u = ["KB", "MB", "GB", "TB"];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < u.length - 1) {
        v /= 1024;
        i++;
    }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${u[i]}`;
}

export interface FilePickerModalProps {
    open: boolean;
    scope: ScopeUrl;
    title?: string;
    /** Pre-select this key (the current value). */
    initialKey?: string;
    /** Restrict the listed files (e.g. only ``.glb``). */
    filter?: (f: FileEntry) => boolean;
    onCancel: () => void;
    onPick: (key: string) => void;
}

const FilePickerModal: React.FC<FilePickerModalProps> = ({
    open,
    scope,
    title = "Pick a file from scope",
    initialKey,
    filter,
    onCancel,
    onPick,
}) => {
    const [files, setFiles] = React.useState<FileEntry[]>([]);
    const [loading, setLoading] = React.useState(false);
    const [picked, setPicked] = React.useState<string | null>(initialKey ?? null);
    const [err, setErr] = React.useState<string | null>(null);

    React.useEffect(() => {
        if (!open) return;
        setPicked(initialKey ?? null);
        setErr(null);
        setLoading(true);
        (async () => {
            try {
                const fs = await viewerApi.listFiles(scope);
                setFiles(filter ? fs.filter(filter) : fs);
            } catch (e) {
                setErr(String(e));
                setFiles([]);
            } finally {
                setLoading(false);
            }
        })();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, scope]);

    React.useEffect(() => {
        if (!open) return;
        const h = (e: KeyboardEvent) => {
            if (e.key === "Escape") onCancel();
        };
        window.addEventListener("keydown", h);
        return () => window.removeEventListener("keydown", h);
    }, [open, onCancel]);

    if (!open) return null;

    const tree = buildFileTree(files, (f) => f.key);
    const selected = new Set(picked ? [picked] : []);
    // Single-select: a file checkbox sets the pick; a folder checkbox takes its last descendant.
    const onSelect = (keys: string[], select: boolean) => {
        if (!select) {
            setPicked((p) => (p && keys.includes(p) ? null : p));
            return;
        }
        setPicked(keys.length ? keys[keys.length - 1] : null);
    };

    return createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onCancel}>
            <div
                className="bg-gray-900 text-gray-100 border border-gray-700 rounded-md shadow-xl w-[36rem] max-w-[92vw] flex flex-col"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="px-4 py-2 border-b border-gray-700 font-medium">{title}</div>
                <div className="px-2 py-2 overflow-auto max-h-[60vh] min-h-[8rem]">
                    {loading ? (
                        <div className="text-xs text-gray-500 px-2 py-4">Loading…</div>
                    ) : files.length === 0 ? (
                        <div className="text-xs text-gray-500 italic px-2 py-4">
                            No files in this scope. Upload comparison files via the storage browser first.
                        </div>
                    ) : (
                        <FileTreeView
                            nodes={tree}
                            getKey={(f) => f.key}
                            namespace="util-ref-pick"
                            scope={String(scope)}
                            selection={{selected, onSelect}}
                            renderFileTail={(f) => (
                                <span className="text-gray-400 font-mono text-xs">{fmtBytes(f.size)}</span>
                            )}
                        />
                    )}
                </div>
                {err && <div className="text-xs text-red-400 px-4 py-1">{err}</div>}
                <div className="px-4 py-2 border-t border-gray-700 flex justify-between items-center gap-2">
                    <span className="text-xs font-mono text-gray-400 truncate flex-1" title={picked ?? ""}>
                        {picked ?? "— nothing selected —"}
                    </span>
                    <button
                        type="button"
                        className="text-sm px-2 py-1 rounded-sm bg-gray-600 text-white"
                        onClick={onCancel}
                    >
                        Cancel
                    </button>
                    <button
                        type="button"
                        className="text-sm px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50"
                        disabled={!picked}
                        onClick={() => picked && onPick(picked)}
                    >
                        Use file
                    </button>
                </div>
            </div>
        </div>,
        document.body,
    );
};

export default FilePickerModal;
