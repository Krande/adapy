import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {Corpus, FileEntry, viewerApi} from "@/services/viewerApi";
import {
    buildFileTree,
    collectFolderPaths,
    loadPendingFolders,
    previewKeyList,
    savePendingFolders,
} from "@/utils/storage/fileTree";
import FileTreeView, {FileTreeMutations} from "./FileTreeView";
import FolderPickerModal from "@/components/common/FolderPickerModal";
import {scopeUrlPart} from "@/state/scopeStore";

// Admin tab — manage proprietary regression corpora (M3 of the audit
// panel design in the admin audit-panel design notes).
//
// Each corpus is its own scope (``corpus:<slug>``) — the per-scope
// /api/scopes/{scope}/files endpoints already exist, so file
// management here is just upload / list / delete against a chosen
// corpus slug. RBAC is admin-only on every axis: scope_can_access
// rejects non-admin reads on the backend.
//
// The trigger form on the Audit Runs tab picks a corpus by slug from
// the same /admin/corpora list this tab maintains.
//
// Tree mode carries the storage panel's organize affordances (via the
// shared FileTreeView mutations): rename / move / delete files and
// folders, drag-and-drop moves, client-side pending folders, and a
// checkbox / shift+arrow multi-select feeding a bulk Move/Delete
// toolbar. Server ops go through the admin endpoints (corpus scopes
// are admin-only on every axis).

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function dirnameOf(key: string): string {
    const i = key.lastIndexOf("/");
    return i >= 0 ? key.slice(0, i) : "";
}

function basenameOf(key: string): string {
    return key.split("/").pop() ?? key;
}

function normKey(key: string): string {
    return key.replace(/^\/+/, "");
}

// Batch size for chunked server-side moves/copies. Every chunk is still
// a Garage-side CopyObject (no file bytes through the browser) — the
// chunking only exists so the progress counter ticks between requests.
const OP_CHUNK = 8;

// Flat-list ⇄ folder-tree representation switch. Storage is flat on the
// server; tree mode just groups the keys' "/" segments. Shared by the
// corpus file overview and the copy-from-scope modal.
type ViewMode = "flat" | "tree";

const ViewModeToggle: React.FC<{mode: ViewMode; onChange: (m: ViewMode) => void}> = ({mode, onChange}) => (
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

const NewCorpusForm: React.FC<{onCreated: () => void}> = ({onCreated}) => {
    const [slug, setSlug] = useState("");
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const onSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        setErr(null);
        if (!SLUG_RE.test(slug)) {
            setErr("slug must be lowercase ASCII with hyphen separators (e.g. cad-baseline)");
            return;
        }
        if (!name.trim()) {
            setErr("name required");
            return;
        }
        setBusy(true);
        try {
            await viewerApi.adminCorpusCreate({
                slug, name: name.trim(),
                description: description.trim() || null,
            });
            setSlug("");
            setName("");
            setDescription("");
            onCreated();
        } catch (e) {
            setErr((e as Error).message || "create failed");
        } finally {
            setBusy(false);
        }
    }, [slug, name, description, onCreated]);

    return (
        <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900/40">
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Slug</span>
                <input
                    type="text"
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="cad-baseline"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 font-mono w-40"
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1">
                <span>Name</span>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="CAD baseline"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 w-48"
                />
            </label>
            <label className="text-xs text-gray-300 flex flex-col gap-1 flex-1 min-w-[180px]">
                <span>Description <span className="text-gray-500">(optional)</span></span>
                <input
                    type="text"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Representative STEP / IFC files for release-gate sweeps"
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                />
            </label>
            <button
                type="submit"
                disabled={busy}
                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm px-3 py-1 rounded-sm h-[30px]"
            >
                {busy ? "Creating…" : "Create corpus"}
            </button>
            {err && (
                <div className="w-full text-xs text-red-400" role="alert">{err}</div>
            )}
        </form>
    );
};

// Pick files from one of the caller's other scopes (user / shared / project)
// and server-side copy the selection into the corpus. Garage CopyObject — no
// download/reupload — so even large STEP files copy instantly.
const CopyFromScopeModal: React.FC<{
    dstScope: string;
    dstSlug: string;
    onClose: () => void;
    onCopied: () => void;
}> = ({dstScope, dstSlug, onClose, onCopied}) => {
    const [scopes, setScopes] = useState<Array<{name: string; url: string}>>([]);
    const [srcScope, setSrcScope] = useState("");
    const [files, setFiles] = useState<FileEntry[]>([]);
    const [loadingFiles, setLoadingFiles] = useState(false);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [filter, setFilter] = useState("");
    const [viewMode, setViewMode] = useState<ViewMode>("flat");
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [result, setResult] = useState<{copied: number; skipped: number; failed: {key: string; reason: string}[]} | null>(null);
    // Destination corpus keys, fetched once — files already in the corpus
    // render greyed "in corpus" and can't be (re)selected; the backend also
    // skips them, so this is purely a clearer-affordance pre-check.
    const [existingKeys, setExistingKeys] = useState<ReadonlySet<string>>(new Set());

    useEffect(() => {
        void (async () => {
            try {
                const xs = await viewerApi.listFiles(dstScope);
                setExistingKeys(new Set(xs.map((f) => f.key)));
            } catch {
                // Best-effort — without it nothing is greyed, but the copy
                // still skips collisions server-side.
            }
        })();
    }, [dstScope]);

    useEffect(() => {
        void (async () => {
            try {
                const me = await viewerApi.me();
                setScopes(
                    me.scopes
                        .map((s) => ({name: s.name, url: scopeUrlPart(s)}))
                        .filter((s) => s.url !== dstScope),
                );
            } catch (e) {
                setErr((e as Error).message || "failed to load scopes");
            }
        })();
    }, [dstScope]);

    useEffect(() => {
        setSelected(new Set());
        setResult(null);
        if (!srcScope) {
            setFiles([]);
            return;
        }
        setLoadingFiles(true);
        void (async () => {
            try {
                setFiles(await viewerApi.listFiles(srcScope));
                setErr(null);
            } catch (e) {
                setErr((e as Error).message || "listing failed");
                setFiles([]);
            } finally {
                setLoadingFiles(false);
            }
        })();
    }, [srcScope]);

    const shown = useMemo(
        () => files.filter((f) => f.key.toLowerCase().includes(filter.toLowerCase())),
        [files, filter],
    );
    const toggle = (key: string) => setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key); else next.add(key);
        return next;
    });
    // Batch select/deselect — folder checkbox in tree mode passes every
    // descendant key at once (recursive select).
    const setSelection = useCallback((keys: string[], select: boolean) => {
        setSelected((prev) => {
            const next = new Set(prev);
            if (select) keys.forEach((k) => next.add(k));
            else keys.forEach((k) => next.delete(k));
            return next;
        });
    }, []);
    const tree = useMemo(() => buildFileTree(shown, (f) => f.key), [shown]);
    const allShownSelected = shown.length > 0 && shown.every((f) => selected.has(f.key));
    const toggleAll = () => setSelected((prev) => {
        const next = new Set(prev);
        if (allShownSelected) shown.forEach((f) => next.delete(f.key));
        else shown.forEach((f) => next.add(f.key));
        return next;
    });

    const onCopy = useCallback(async () => {
        if (selected.size === 0) return;
        setBusy(true);
        setErr(null);
        try {
            const r = await viewerApi.adminCopyKeysFromScope(dstScope, srcScope, Array.from(selected));
            setResult({copied: r.copied.length, skipped: r.skipped.length, failed: r.failed});
            // Copied keys are now "in corpus" — fold them in so a follow-up
            // copy greys them out too without a modal reopen.
            if (r.copied.length > 0) {
                setExistingKeys((prev) => {
                    const next = new Set(prev);
                    r.copied.forEach((c) => next.add(c.key));
                    return next;
                });
            }
            onCopied();
            if (r.failed.length === 0) setSelected(new Set());
        } catch (e) {
            setErr((e as Error).message || "copy failed");
        } finally {
            setBusy(false);
        }
    }, [dstScope, srcScope, selected, onCopied]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
            <div
                className="bg-gray-900 border border-gray-700 rounded-md w-full max-w-2xl max-h-[85vh] flex flex-col"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="px-4 py-3 border-b border-gray-700 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-100">
                        Copy files into <span className="font-mono">{dstSlug}</span>
                    </h3>
                    <button type="button" onClick={onClose} className="text-gray-400 hover:text-white text-lg leading-none px-1">×</button>
                </div>
                <div className="px-4 py-3 border-b border-gray-700 flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-gray-300">From scope</span>
                    <select
                        value={srcScope}
                        onChange={(e) => setSrcScope(e.target.value)}
                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                    >
                        <option value="">Select a scope…</option>
                        {scopes.map((s) => (
                            <option key={s.url} value={s.url}>{s.name} ({s.url})</option>
                        ))}
                    </select>
                    {files.length > 0 && (
                        <input
                            type="text"
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            placeholder="filter…"
                            className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 flex-1 min-w-[120px]"
                        />
                    )}
                    {files.length > 0 && (
                        <ViewModeToggle mode={viewMode} onChange={setViewMode}/>
                    )}
                </div>
                <div className="flex-1 min-h-0 overflow-auto">
                    {loadingFiles && <div className="text-xs text-gray-500 px-4 py-4">Loading…</div>}
                    {!loadingFiles && srcScope && shown.length === 0 && (
                        <div className="text-xs text-gray-500 italic px-4 py-4">
                            No files{filter ? " match the filter" : " in this scope"}.
                        </div>
                    )}
                    {shown.length > 0 && viewMode === "flat" && (
                        <table className="w-full text-xs">
                            <thead className="sticky top-0 bg-gray-900">
                                <tr>
                                    <th className="px-3 py-1 border-b border-gray-800 w-8">
                                        <input type="checkbox" checked={allShownSelected} onChange={toggleAll}/>
                                    </th>
                                    <th className="text-left px-3 py-1 border-b border-gray-800 font-medium text-gray-300">Key</th>
                                    <th className="text-right px-3 py-1 border-b border-gray-800 font-medium text-gray-300">Size</th>
                                </tr>
                            </thead>
                            <tbody>
                                {shown.map((f) => (
                                    <tr key={f.key} className="hover:bg-gray-800/40 cursor-pointer" onClick={() => toggle(f.key)}>
                                        <td className="px-3 py-1 border-b border-gray-800 text-center">
                                            <input
                                                type="checkbox"
                                                checked={selected.has(f.key)}
                                                onChange={() => toggle(f.key)}
                                                onClick={(e) => e.stopPropagation()}
                                            />
                                        </td>
                                        <td className="font-mono text-gray-200 px-3 py-1 border-b border-gray-800 truncate max-w-md">{f.key}</td>
                                        <td className="text-right text-gray-400 px-3 py-1 border-b border-gray-800 font-mono">{fmtBytes(f.size)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                    {shown.length > 0 && viewMode === "tree" && (
                        <div className="px-1 py-1">
                            <FileTreeView
                                nodes={tree}
                                getKey={(f) => f.key}
                                namespace="corpus-copy"
                                scope={srcScope}
                                selection={{selected, onSelect: setSelection}}
                                isDisabled={(f) => existingKeys.has(f.key)}
                                renderFileTail={(f) => (
                                    existingKeys.has(f.key) ? (
                                        <span className="text-[10px] text-gray-500 uppercase tracking-wide">in corpus</span>
                                    ) : (
                                        <span className="text-gray-400 font-mono">{fmtBytes(f.size)}</span>
                                    )
                                )}
                            />
                        </div>
                    )}
                </div>
                {err && <div className="text-xs text-red-400 px-4 py-2">{err}</div>}
                {result && (
                    <div className="text-xs px-4 py-2 border-t border-gray-700">
                        <span className="text-emerald-400">copied {result.copied}</span>
                        {result.skipped > 0 && (
                            <span className="text-gray-400">
                                {" "}· skipped {result.skipped} (already in corpus)
                            </span>
                        )}
                        {result.failed.length > 0 && (
                            <span className="text-amber-400" title={result.failed.map((f) => `${f.key}: ${f.reason}`).join("\n")}>
                                {" "}· failed {result.failed.length}
                            </span>
                        )}
                    </div>
                )}
                <div className="px-4 py-3 border-t border-gray-700 flex justify-end gap-2">
                    <button type="button" onClick={onClose} className="text-sm px-3 py-1 rounded-sm text-gray-300 hover:bg-gray-800">Close</button>
                    <button
                        type="button"
                        onClick={() => void onCopy()}
                        disabled={busy || selected.size === 0}
                        className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                    >
                        {busy ? "Copying…" : `Copy ${selected.size || ""} file${selected.size === 1 ? "" : "s"}`.trim()}
                    </button>
                </div>
            </div>
        </div>
    );
};

const CorpusFiles: React.FC<{
    corpus: Corpus;
    /** Name/description saved — the parent re-fetches the corpora list
     * so the sidebar and this header pick up the new values. */
    onMetaUpdated: () => void;
}> = ({corpus, onMetaUpdated}) => {
    const scope = `corpus:${corpus.slug}`;
    const [files, setFiles] = useState<FileEntry[]>([]);
    const [err, setErr] = useState<string | null>(null);
    // Transient success line (e.g. copy-to-personal outcome).
    const [note, setNote] = useState<string | null>(null);
    // In-flight batch operation (move / delete / copy) — rendered as a
    // spinner status bar under the button row so a drag-drop move of
    // many files visibly runs until the listing refreshes. The ref
    // mirrors it so callbacks can reject overlapping batches without
    // stale-closure issues (concurrent moves would race server-side).
    const [busy, setBusy] = useState<string | null>(null);
    const busyRef = useRef(false);
    const beginOp = (msg: string): boolean => {
        if (busyRef.current) return false;
        busyRef.current = true;
        setBusy(msg);
        return true;
    };
    const updateOp = (msg: string) => setBusy(msg);
    const endOp = () => {
        busyRef.current = false;
        setBusy(null);
    };
    const [uploading, setUploading] = useState<string | null>(null);
    const [progress, setProgress] = useState(0);
    const [copyOpen, setCopyOpen] = useState(false);
    // Flat ⇄ tree representation, persisted per corpus so a chosen view
    // sticks across reloads / corpus switches.
    const [viewMode, setViewMode] = useState<ViewMode>(() => {
        try {
            return window.localStorage.getItem(`ada.corpus.viewMode.${corpus.slug}`) === "tree" ? "tree" : "flat";
        } catch {
            return "flat";
        }
    });
    useEffect(() => {
        try {
            window.localStorage.setItem(`ada.corpus.viewMode.${corpus.slug}`, viewMode);
        } catch {
            // localStorage full / disabled — fall back to in-memory only.
        }
    }, [corpus.slug, viewMode]);
    const inputRef = useRef<HTMLInputElement>(null);

    const reload = useCallback(async () => {
        try {
            const xs = await viewerApi.listFiles(scope);
            setFiles(xs);
            setErr(null);
        } catch (e) {
            setErr((e as Error).message || "listing failed");
        }
    }, [scope]);

    useEffect(() => { void reload(); }, [reload]);

    // Client-side "pending" empty folders — storage is prefix-based so
    // they have no server representation until a file lands in them.
    // Persisted per corpus scope; pruned once a real key appears
    // underneath (same mechanics as StorageBrowser).
    const [pendingFolders, setPendingFolders] = useState<string[]>(
        () => loadPendingFolders("corpus", scope),
    );
    useEffect(() => {
        savePendingFolders("corpus", scope, pendingFolders);
    }, [scope, pendingFolders]);
    useEffect(() => {
        setPendingFolders((prev) => {
            const next = prev.filter(
                (p) => !files.some((f) => normKey(f.key).startsWith(p + "/")),
            );
            return next.length === prev.length ? prev : next;
        });
    }, [files]);
    const removePendingFoldersUnder = (path: string) => {
        setPendingFolders((prev) =>
            prev.filter((p) => p !== path && !p.startsWith(path + "/")),
        );
    };
    // Rename/move of a pending (empty) folder is pure client state.
    const rekeyPendingFolders = (oldPath: string, newPath: string) => {
        setPendingFolders((prev) => prev.map((p) => (
            p === oldPath
                ? newPath
                : p.startsWith(oldPath + "/")
                    ? newPath + p.slice(oldPath.length)
                    : p
        )));
    };
    const folderHasKeys = useCallback(
        (path: string) => files.some((f) => normKey(f.key).startsWith(path + "/")),
        [files],
    );

    // Inline name/description editor in the header. The slug is
    // immutable (storage prefix + scope URLs hang off it), so only the
    // display fields are editable. Seeded from the current corpus row
    // each time the editor opens.
    const [editingMeta, setEditingMeta] = useState(false);
    const [metaName, setMetaName] = useState("");
    const [metaDesc, setMetaDesc] = useState("");
    const [metaBusy, setMetaBusy] = useState(false);
    const openMetaEdit = () => {
        setMetaName(corpus.name);
        setMetaDesc(corpus.description ?? "");
        setEditingMeta(true);
    };
    const saveMeta = async () => {
        const name = metaName.trim();
        if (!name) {
            setErr("name required");
            return;
        }
        setMetaBusy(true);
        try {
            await viewerApi.adminCorpusUpdate(corpus.slug, {
                name,
                description: metaDesc.trim() || null,
            });
            setEditingMeta(false);
            setErr(null);
            onMetaUpdated();
        } catch (e) {
            setErr((e as Error).message || "corpus update failed");
        } finally {
            setMetaBusy(false);
        }
    };

    // Where the tree's "new folder" inline input shows ("" = top level).
    const [newFolderAt, setNewFolderAt] = useState<string | null>(null);
    // Destination-folder modal shared by the upload and move flows.
    const [picker, setPicker] = useState<{
        title: string;
        allowRoot?: boolean;
        submitLabel?: string;
        onPick: (folder: string) => Promise<void> | void;
    } | null>(null);

    // Multi-select (tree mode): checkbox / shift+arrow selection set
    // feeding the bulk Move/Delete toolbar. Dragging a selected row
    // drags the whole set (FileTreeView handles that).
    const [selected, setSelected] = useState<Set<string>>(() => new Set());
    const setSelection = useCallback((keys: string[], select: boolean) => {
        setSelected((prev) => {
            const next = new Set(prev);
            if (select) keys.forEach((k) => next.add(k));
            else keys.forEach((k) => next.delete(k));
            return next;
        });
    }, []);
    const clearSelection = () => setSelected(new Set());
    // Drop selection entries whose keys vanished (moved/renamed/deleted).
    useEffect(() => {
        setSelected((prev) => {
            const live = new Set(files.map((f) => f.key));
            const next = new Set(Array.from(prev).filter((k) => live.has(k)));
            return next.size === prev.size ? prev : next;
        });
    }, [files]);

    const existingFolderPaths = useMemo(
        () => Array.from(new Set([
            ...collectFolderPaths(files, (f) => f.key),
            ...pendingFolders,
        ])).sort((a, b) => a.localeCompare(b)),
        [files, pendingFolders],
    );

    // Failed uploads from the last batch — drives the retry dialog.
    // Holds the actual File objects so Retry can re-attempt without
    // re-picking them from disk.
    const [uploadFailures, setUploadFailures] = useState<{
        folder?: string;
        failed: Array<{file: File; reason: string}>;
    } | null>(null);

    // Upload a batch sequentially into an optional folder prefix. Pin
    // autoConvert:false so we don't auto-generate derived blobs for
    // corpus uploads — the audit dispatcher does that on demand when
    // the sweep fires. Files whose destination key already exists in
    // the corpus are skipped, never overwritten (same semantics as the
    // copy flows). A failed file doesn't abort the batch — failures
    // land in the retry dialog.
    const uploadFilesTo = useCallback(async (list: File[], folder?: string) => {
        if (list.length === 0) return;
        setErr(null);
        setNote(null);
        const existing = new Set(files.map((f) => normKey(f.key)));
        const targetKey = (file: File) => (folder ? `${folder}/${file.name}` : file.name);
        const skipped = list.filter((f) => existing.has(targetKey(f)));
        const toUpload = list.filter((f) => !existing.has(targetKey(f)));
        const {uploadFile} = await import("@/utils/scene/handlers/upload_source_file");
        const failed: Array<{file: File; reason: string}> = [];
        for (let i = 0; i < toUpload.length; i++) {
            const file = toUpload[i];
            setUploading(toUpload.length > 1 ? `${file.name} (${i + 1}/${toUpload.length})` : file.name);
            setProgress(0);
            try {
                await uploadFile(file, {
                    autoConvert: false,
                    scope,
                    folder,
                    onProgress: (loaded, total) => setProgress(total > 0 ? loaded / total : 0),
                });
            } catch (e) {
                failed.push({file, reason: (e as Error).message || "upload failed"});
            }
        }
        setUploading(null);
        setProgress(0);
        await reload();
        const bits: string[] = [];
        const uploaded = toUpload.length - failed.length;
        if (uploaded > 0) bits.push(`uploaded ${uploaded}`);
        if (skipped.length > 0) bits.push(`skipped ${skipped.length} (already in corpus)`);
        if (bits.length > 0) setNote(bits.join(" · "));
        if (failed.length > 0) setUploadFailures({folder, failed});
    }, [files, scope, reload]);

    // Upload button flow: pick the files first, then prompt for the
    // destination folder — an existing folder, a new path, or the top
    // level (the default).
    const onPickUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const picked = Array.from(e.target.files ?? []);
        if (inputRef.current) inputRef.current.value = "";
        if (picked.length === 0) return;
        setPicker({
            title: `Upload ${picked.length} file${picked.length === 1 ? "" : "s"} to`,
            allowRoot: true,
            submitLabel: "Upload",
            onPick: (folder) => void uploadFilesTo(picked, folder || undefined),
        });
    }, [uploadFilesTo]);

    const onDelete = useCallback(async (key: string) => {
        if (!confirm(`Delete ${key} from ${corpus.slug}? This can't be undone.`)) return;
        try {
            await viewerApi.adminDeleteBlob(scope, key);
            await reload();
        } catch (e) {
            setErr((e as Error).message || "delete failed");
        }
    }, [scope, corpus.slug, reload]);

    const alertFailures = (failed: Array<{key: string; reason: string}>) => {
        if (failed.length > 0) {
            window.alert(failed.map((f) => `${f.key}: ${f.reason}`).join("\n"));
        }
    };

    const runFolderMove = useCallback(async (folderPath: string, newPath: string) => {
        if (newPath === folderPath) return;
        const count = files.filter((f) => normKey(f.key).startsWith(folderPath + "/")).length;
        if (!beginOp(
            `Moving folder "${folderPath}" → "${newPath}" (${count} file${count === 1 ? "" : "s"})…`,
        )) return;
        try {
            const allKeys = files.map((f) => f.key);
            const r = await viewerApi.adminRenameOrMoveFolder(scope, folderPath, newPath, allKeys);
            alertFailures(r.failed);
            removePendingFoldersUnder(folderPath);
            await reload();
        } catch (e) {
            setErr((e as Error).message || "folder move failed");
        } finally {
            endOp();
        }
    }, [files, scope, reload]);

    const moveKeys = useCallback(async (keys: string[], destFolder: string) => {
        const label = destFolder ? `${destFolder}/` : "root /";
        if (!beginOp(`Moving 0/${keys.length} to ${label}…`)) return;
        try {
            if (destFolder === "") {
                // Move-to-root: the move endpoint requires a non-empty
                // folder, so root moves are per-key renames to the
                // basename.
                let done = 0;
                for (const k of keys) {
                    updateOp(`Moving ${done + 1}/${keys.length} to ${label}…`);
                    await viewerApi.adminRenameKey(scope, k, basenameOf(k));
                    done++;
                }
            } else {
                const failed: Array<{key: string; reason: string}> = [];
                for (let i = 0; i < keys.length; i += OP_CHUNK) {
                    const chunk = keys.slice(i, i + OP_CHUNK);
                    updateOp(`Moving ${Math.min(i + chunk.length, keys.length)}/${keys.length} to ${label}…`);
                    const r = await viewerApi.adminMoveKeysToFolder(scope, chunk, destFolder);
                    failed.push(...r.failed);
                }
                alertFailures(failed);
            }
            clearSelection();
            await reload();
        } catch (e) {
            setErr((e as Error).message || "move failed");
        } finally {
            endOp();
        }
    }, [scope, reload]);

    // Folder subtree lands at ``destFolder``/``basename`` ("" = root).
    const moveFolderTo = (path: string, destFolder: string) => {
        const base = basenameOf(path);
        const newPath = destFolder ? `${destFolder}/${base}` : base;
        if (!folderHasKeys(path)) {
            rekeyPendingFolders(path, newPath);
            return;
        }
        void runFolderMove(path, newPath);
    };

    // Shared by the bulk toolbar and the Delete key: confirm with an
    // overview of exactly what goes, then delete sequentially.
    const deleteKeysWithConfirm = useCallback(async (keys: string[]) => {
        if (keys.length === 0) return;
        if (!confirm(
            `Delete ${keys.length} file${keys.length === 1 ? "" : "s"} from ${corpus.slug}? ` +
            "This can't be undone.\n\n" +
            previewKeyList(keys),
        )) return;
        if (!beginOp(`Deleting 0/${keys.length}…`)) return;
        try {
            let done = 0;
            for (const k of keys) {
                updateOp(`Deleting ${done + 1}/${keys.length}…`);
                await viewerApi.adminDeleteBlob(scope, k);
                done++;
            }
            setSelected(new Set());
            await reload();
        } catch (e) {
            setErr((e as Error).message || "delete failed");
        } finally {
            endOp();
        }
    }, [scope, corpus.slug, reload]);

    // Server-side copy (Garage CopyObject) corpus → the caller's
    // personal scope, preserving keys. Existing keys are skipped, not
    // overwritten — same semantics as the copy-into-corpus modal.
    const copyToPersonal = useCallback(async (keys: string[]) => {
        if (keys.length === 0) return;
        if (!beginOp(`Copying 0/${keys.length} to your files…`)) return;
        setNote(null);
        try {
            let copied = 0;
            let skipped = 0;
            const failed: Array<{key: string; reason: string}> = [];
            for (let i = 0; i < keys.length; i += OP_CHUNK) {
                const chunk = keys.slice(i, i + OP_CHUNK);
                updateOp(`Copying ${Math.min(i + chunk.length, keys.length)}/${keys.length} to your files…`);
                const r = await viewerApi.adminCopyKeysFromScope("user:me", scope, chunk);
                copied += r.copied.length;
                skipped += r.skipped.length;
                failed.push(...r.failed);
            }
            alertFailures(failed);
            setNote(
                `copied ${copied} to your files` +
                (skipped > 0 ? ` · skipped ${skipped} (already there)` : ""),
            );
        } catch (e) {
            setErr((e as Error).message || "copy to personal scope failed");
        } finally {
            endOp();
        }
    }, [scope]);

    const mutations: FileTreeMutations = {
        renameFile: (key, newName) => {
            const dir = dirnameOf(key);
            const newKey = dir ? `${dir}/${newName}` : newName;
            void (async () => {
                try {
                    await viewerApi.adminRenameKey(scope, key, newKey);
                    await reload();
                } catch (e) {
                    setErr((e as Error).message || "rename failed");
                }
            })();
        },
        renameFolder: (path, newName) => {
            const parent = dirnameOf(path);
            const newPath = parent ? `${parent}/${newName}` : newName;
            if (!folderHasKeys(path)) {
                rekeyPendingFolders(path, newPath);
                return;
            }
            void runFolderMove(path, newPath);
        },
        moveKeys: (keys, destFolder) => void moveKeys(keys, destFolder),
        moveFolder: moveFolderTo,
        deleteFile: (key) => void onDelete(key),
        deleteFolder: (path, fileCount) => {
            if (fileCount === 0) {
                // Pending (empty) folder — pure client state.
                removePendingFoldersUnder(path);
                return;
            }
            const prefix = path + "/";
            const targets = files.filter((f) => normKey(f.key).startsWith(prefix));
            if (!confirm(
                `Delete folder "${path}" and its ${fileCount} file${fileCount === 1 ? "" : "s"} ` +
                `from ${corpus.slug}? This can't be undone.\n\n` +
                previewKeyList(targets.map((t) => t.key)),
            )) return;
            void (async () => {
                if (!beginOp(`Deleting 0/${targets.length} from "${path}"…`)) return;
                try {
                    // Sequential: deletes cascade derived blobs server-side
                    // and parallel calls would race on the storage listing.
                    let done = 0;
                    for (const t of targets) {
                        updateOp(`Deleting ${done + 1}/${targets.length} from "${path}"…`);
                        await viewerApi.adminDeleteBlob(scope, t.key);
                        done++;
                    }
                    removePendingFoldersUnder(path);
                    await reload();
                } catch (e) {
                    setErr((e as Error).message || "folder delete failed");
                } finally {
                    endOp();
                }
            })();
        },
        createFolder: (parent, name) => {
            // ``_derived`` is where the converter parks derived blobs —
            // a user folder with that name would collide with the cache
            // prefix.
            if (!parent && name === "_derived") {
                window.alert(`"${name}" is a reserved name`);
                return;
            }
            const path = parent ? `${parent}/${name}` : name;
            setPendingFolders((prev) => (prev.includes(path) ? prev : [...prev, path]));
        },
        requestMoveFile: (key) => setPicker({
            title: `Move "${key}" to folder`,
            onPick: (folder) => void moveKeys([key], folder),
        }),
        requestMoveFolder: (path) => setPicker({
            title: `Move folder "${path}" into`,
            onPick: (dest) => moveFolderTo(path, dest),
        }),
        deleteKeys: (keys) => void deleteKeysWithConfirm(keys),
        uploadTo: (folder, list) => void uploadFilesTo(list, folder || undefined),
        downloadFile: (key) => void viewerApi.downloadBlob(scope, key, basenameOf(key)),
    };

    const onMoveSelected = () => {
        const keys = Array.from(selected);
        if (keys.length === 0) return;
        setPicker({
            title: `Move ${keys.length} file${keys.length === 1 ? "" : "s"} to folder`,
            onPick: (folder) => void moveKeys(keys, folder),
        });
    };

    const showTree = viewMode === "tree" &&
        (files.length > 0 || pendingFolders.length > 0 || newFolderAt !== null);

    return (
        <div className="flex flex-col h-full">
            <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-3">
                {!editingMeta ? (
                    <div className="text-xs text-gray-300 min-w-0 flex items-start gap-1.5">
                        <div className="min-w-0">
                            <div className="font-mono truncate">
                                {corpus.slug}
                                <span className="text-gray-400 font-sans"> · {corpus.name}</span>
                            </div>
                            {corpus.description && (
                                <div className="text-gray-500 truncate">{corpus.description}</div>
                            )}
                        </div>
                        <button
                            type="button"
                            onClick={openMetaEdit}
                            title="Edit name / description (slug is immutable)"
                            aria-label="Edit corpus name and description"
                            className="shrink-0 text-gray-500 hover:text-gray-200 leading-none px-1"
                        >
                            ✎
                        </button>
                    </div>
                ) : (
                    <form
                        className="flex flex-col gap-1 min-w-0 flex-1 max-w-md text-xs"
                        onSubmit={(e) => {
                            e.preventDefault();
                            void saveMeta();
                        }}
                    >
                        <div className="font-mono text-gray-500">{corpus.slug}</div>
                        <input
                            type="text"
                            value={metaName}
                            onChange={(e) => setMetaName(e.target.value)}
                            placeholder="Name"
                            autoFocus
                            className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-gray-100"
                        />
                        <input
                            type="text"
                            value={metaDesc}
                            onChange={(e) => setMetaDesc(e.target.value)}
                            placeholder="Description (optional)"
                            onKeyDown={(e) => {
                                if (e.key === "Escape") setEditingMeta(false);
                            }}
                            className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-gray-100"
                        />
                        <div className="flex gap-2">
                            <button
                                type="submit"
                                disabled={metaBusy}
                                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm"
                            >
                                {metaBusy ? "Saving…" : "Save"}
                            </button>
                            <button
                                type="button"
                                onClick={() => setEditingMeta(false)}
                                disabled={metaBusy}
                                className="text-gray-300 hover:bg-gray-800 px-2 py-0.5 rounded-sm"
                            >
                                Cancel
                            </button>
                        </div>
                    </form>
                )}
                <div className="flex items-center gap-2 shrink-0">
                    <ViewModeToggle mode={viewMode} onChange={setViewMode}/>
                    {viewMode === "tree" && (
                        <button
                            type="button"
                            onClick={() => setNewFolderAt("")}
                            disabled={!!uploading}
                            className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                        >
                            New folder
                        </button>
                    )}
                    <input
                        ref={inputRef}
                        type="file"
                        multiple
                        onChange={onPickUpload}
                        className="hidden"
                        disabled={!!uploading}
                    />
                    <button
                        type="button"
                        onClick={() => setCopyOpen(true)}
                        disabled={!!uploading}
                        className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                    >
                        Copy from scope…
                    </button>
                    <button
                        type="button"
                        onClick={() => inputRef.current?.click()}
                        disabled={!!uploading}
                        className="bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                    >
                        {uploading
                            ? `Uploading ${Math.round(progress * 100)}%`
                            : "Upload file"}
                    </button>
                </div>
            </div>
            {copyOpen && (
                <CopyFromScopeModal
                    dstScope={scope}
                    dstSlug={corpus.slug}
                    onClose={() => setCopyOpen(false)}
                    onCopied={() => void reload()}
                />
            )}
            {busy && (
                <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800 bg-blue-900/20 text-xs text-blue-300">
                    <span
                        className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0"
                        aria-hidden="true"
                    />
                    <span className="truncate" role="status">{busy}</span>
                </div>
            )}
            {err && (
                <div className="text-xs text-red-400 px-3 py-2">{err}</div>
            )}
            {note && !err && !busy && (
                <div className="text-xs text-emerald-400 px-3 py-2">{note}</div>
            )}
            {viewMode === "tree" && selected.size > 0 && (
                <div className="mx-3 my-2 px-2 py-1.5 rounded-sm border border-gray-700 bg-gray-800/95 flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-white whitespace-nowrap">
                        {selected.size} selected
                    </span>
                    <button
                        type="button"
                        onClick={onMoveSelected}
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Move…
                    </button>
                    <button
                        type="button"
                        onClick={() => void copyToPersonal(Array.from(selected))}
                        title="Server-side copy into your personal scope (same keys; existing files are skipped)"
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Copy to my files
                    </button>
                    <button
                        type="button"
                        onClick={() => void deleteKeysWithConfirm(Array.from(selected))}
                        className="bg-red-800 hover:bg-red-700 active:bg-red-900 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Delete
                    </button>
                    <button
                        type="button"
                        onClick={clearSelection}
                        className="ml-auto bg-gray-600 hover:bg-gray-500 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Cancel
                    </button>
                </div>
            )}
            <div className="flex-1 min-h-0 overflow-auto">
                {files.length === 0 && !err && pendingFolders.length === 0 && newFolderAt === null && (
                    <div className="text-xs text-gray-500 italic px-3 py-4">
                        No files yet. Upload representative source files (STEP /
                        IFC / RMED / etc.) to drive regression sweeps from the
                        Audit Runs tab.
                    </div>
                )}
                {files.length > 0 && viewMode === "flat" && (
                    <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-gray-900">
                            <tr>
                                <th className="text-left px-3 py-1 border-b border-gray-800 font-medium text-gray-300">
                                    Key
                                </th>
                                <th className="text-right px-3 py-1 border-b border-gray-800 font-medium text-gray-300">
                                    Size
                                </th>
                                <th className="px-3 py-1 border-b border-gray-800"/>
                            </tr>
                        </thead>
                        <tbody>
                            {files.map((f) => (
                                <tr key={f.key} className="hover:bg-gray-800/40">
                                    <td className="font-mono text-gray-200 px-3 py-1 border-b border-gray-800 truncate max-w-md">
                                        {f.key}
                                    </td>
                                    <td className="text-right text-gray-400 px-3 py-1 border-b border-gray-800 font-mono">
                                        {fmtBytes(f.size)}
                                    </td>
                                    <td className="text-right px-3 py-1 border-b border-gray-800">
                                        <button
                                            type="button"
                                            onClick={() => void onDelete(f.key)}
                                            className="text-red-400 hover:text-red-300 text-xs"
                                        >
                                            delete
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
                {showTree && (
                    <div className="px-1 py-1">
                        <FileTreeView
                            nodes={buildFileTree(files, (f) => f.key, pendingFolders)}
                            getKey={(f) => f.key}
                            namespace="corpus"
                            scope={scope}
                            selection={{selected, onSelect: setSelection}}
                            mutations={mutations}
                            extraFileMenuItems={(key) => [{
                                key: "copy-to-personal",
                                label: "Copy to my files",
                                title: "Server-side copy into your personal scope (same key; skipped if it already exists)",
                                onClick: () => void copyToPersonal([key]),
                            }]}
                            newFolderAt={newFolderAt}
                            onNewFolderAtChange={setNewFolderAt}
                            renderFileTail={(f) => (
                                <span className="text-gray-400 font-mono">{fmtBytes(f.size)}</span>
                            )}
                        />
                    </div>
                )}
            </div>
            <FolderPickerModal
                open={picker !== null}
                title={picker?.title ?? ""}
                existingFolders={existingFolderPaths}
                allowRoot={picker?.allowRoot}
                submitLabel={picker?.submitLabel}
                onCancel={() => setPicker(null)}
                onPick={(folder) => {
                    const action = picker?.onPick;
                    setPicker(null);
                    if (action) void action(folder);
                }}
            />
            {/* Failed-uploads overview + retry. Not portaled — like the
                copy-from-scope modal it renders inside the admin panel's
                stacking context, so z-50 suffices here. Retry re-runs
                only the failed files (the skip-existing pre-check makes
                a partially-succeeded upload a safe no-op). */}
            {uploadFailures && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
                    <div className="bg-gray-900 border border-gray-700 rounded-md w-full max-w-lg max-h-[70vh] flex flex-col">
                        <div className="px-4 py-3 border-b border-gray-700 text-sm font-semibold text-gray-100">
                            {uploadFailures.failed.length} upload{uploadFailures.failed.length === 1 ? "" : "s"} failed
                            {uploadFailures.folder ? (
                                <span className="text-gray-400 font-normal"> → {uploadFailures.folder}/</span>
                            ) : null}
                        </div>
                        <div className="flex-1 min-h-0 overflow-auto px-4 py-2">
                            <ul className="space-y-1 text-xs">
                                {uploadFailures.failed.map(({file, reason}) => (
                                    <li key={file.name} className="flex justify-between items-baseline gap-3">
                                        <span className="font-mono text-gray-200 truncate" title={file.name}>
                                            {file.name}
                                        </span>
                                        <span className="text-red-400 truncate shrink-0 max-w-[50%]" title={reason}>
                                            {reason}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                        <div className="px-4 py-3 border-t border-gray-700 flex justify-end gap-2">
                            <button
                                type="button"
                                onClick={() => setUploadFailures(null)}
                                className="text-sm px-3 py-1 rounded-sm text-gray-300 hover:bg-gray-800"
                            >
                                Close
                            </button>
                            <button
                                type="button"
                                disabled={!!uploading}
                                onClick={() => {
                                    const {folder, failed} = uploadFailures;
                                    setUploadFailures(null);
                                    void uploadFilesTo(failed.map((f) => f.file), folder);
                                }}
                                className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                            >
                                Retry {uploadFailures.failed.length} file{uploadFailures.failed.length === 1 ? "" : "s"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

const CorpusTab: React.FC = () => {
    const [corpora, setCorpora] = useState<Corpus[]>([]);
    const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
    const [listError, setListError] = useState<string | null>(null);

    const loadCorpora = useCallback(async () => {
        try {
            const r = await viewerApi.adminCorporaList();
            setCorpora(r.corpora);
            setListError(null);
        } catch (e) {
            setListError((e as Error).message || "failed to load corpora");
        }
    }, []);

    useEffect(() => { void loadCorpora(); }, [loadCorpora]);

    const selected = useMemo(
        () => corpora.find((c) => c.slug === selectedSlug) || null,
        [corpora, selectedSlug],
    );

    const onArchive = useCallback(async (slug: string) => {
        if (!confirm(
            `Archive ${slug}? Storage bytes survive — wipe those separately ` +
            `if you need the space back. The slug can be reused right away.`,
        )) return;
        try {
            await viewerApi.adminCorpusArchive(slug);
            if (selectedSlug === slug) setSelectedSlug(null);
            await loadCorpora();
        } catch (e) {
            setListError((e as Error).message || "archive failed");
        }
    }, [selectedSlug, loadCorpora]);

    const showList = !selectedSlug;

    return (
        <div className="flex flex-col h-full">
            <NewCorpusForm onCreated={loadCorpora}/>

            <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
                {/* Corpus list — full-width on mobile (collapses when
                    a corpus is selected), sidebar on md+.
                    Mobile scroll wiring matches AuditRunsTab: needs
                    flex-1 min-h-0 in the column-flex context so
                    overflow-auto can actually shrink below content
                    size. md:flex-none restores the fixed sidebar
                    sizing in the row layout. */}
                <div className={
                    "md:w-72 md:shrink-0 md:flex-none md:border-r md:border-b-0 " +
                    "flex-1 min-h-0 border-b border-gray-800 overflow-auto " +
                    (showList ? "block" : "hidden md:block")
                }>
                    {listError && (
                        <div className="text-xs text-red-400 px-3 py-2">{listError}</div>
                    )}
                    {corpora.length === 0 && !listError && (
                        <div className="text-xs text-gray-500 italic px-3 py-4">
                            No corpora yet. Use the form above to create one.
                        </div>
                    )}
                    <ul className="text-xs">
                        {corpora.map((c) => {
                            const active = c.slug === selectedSlug;
                            return (
                                <li
                                    key={c.id}
                                    onClick={() => setSelectedSlug(c.slug)}
                                    className={
                                        "px-3 py-2 border-b border-gray-800 cursor-pointer " +
                                        (active ? "bg-blue-900/40" : "hover:bg-gray-800/40")
                                    }
                                >
                                    <div className="flex justify-between items-baseline gap-2">
                                        <span className="font-mono text-gray-200 truncate">
                                            {c.slug}
                                        </span>
                                        <button
                                            type="button"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                void onArchive(c.slug);
                                            }}
                                            className="text-red-400 hover:text-red-300 text-[10px] shrink-0"
                                        >
                                            archive
                                        </button>
                                    </div>
                                    <div className="text-gray-400 text-[11px] mt-0.5 truncate">
                                        {c.name}
                                    </div>
                                    {c.description && (
                                        <div className="text-gray-500 text-[10px] mt-0.5 truncate" title={c.description}>
                                            {c.description}
                                        </div>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                </div>

                {/* Per-corpus files — hidden on mobile when no corpus
                    is selected. */}
                <div className={
                    "flex-1 min-h-0 flex-col overflow-hidden " +
                    (showList ? "hidden md:flex" : "flex")
                }>
                    {!selected && (
                        <div className="hidden md:block text-xs text-gray-500 italic px-4 py-6">
                            Pick a corpus from the list to manage its files.
                        </div>
                    )}
                    {selected && (
                        <>
                            <div className="md:hidden px-3 py-2 border-b border-gray-800">
                                <button
                                    type="button"
                                    onClick={() => setSelectedSlug(null)}
                                    className="text-sm text-blue-400 hover:text-blue-300"
                                >
                                    ← corpora
                                </button>
                            </div>
                            <CorpusFiles key={selected.slug} corpus={selected} onMetaUpdated={loadCorpora}/>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default CorpusTab;
