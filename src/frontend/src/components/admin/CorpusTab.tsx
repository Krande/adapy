import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {Corpus, FileEntry, viewerApi} from "@/services/viewerApi";

// Admin tab — manage proprietary regression corpora (M3 of the audit
// panel design in plan/v2/notes_admin_audit_panel.md).
//
// Each corpus is its own scope (``corpus:<slug>``) — the per-scope
// /api/scopes/{scope}/files endpoints already exist, so file
// management here is just upload / list / delete against a chosen
// corpus slug. RBAC is admin-only on every axis: scope_can_access
// rejects non-admin reads on the backend.
//
// The trigger form on the Audit Runs tab picks a corpus by slug from
// the same /admin/corpora list this tab maintains.

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

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

const CorpusFiles: React.FC<{corpus: Corpus}> = ({corpus}) => {
    const scope = `corpus:${corpus.slug}`;
    const [files, setFiles] = useState<FileEntry[]>([]);
    const [err, setErr] = useState<string | null>(null);
    const [uploading, setUploading] = useState<string | null>(null);
    const [progress, setProgress] = useState(0);
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

    const onPick = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        setErr(null);
        setUploading(file.name);
        setProgress(0);
        try {
            // Reuse the existing per-scope upload path. Pin
            // autoConvert:false so we don't auto-generate derived
            // blobs for corpus uploads — the audit dispatcher does
            // that on demand when the sweep fires.
            const {uploadFile} = await import("@/utils/scene/handlers/upload_source_file");
            await uploadFile(file, {
                autoConvert: false,
                scope,
                onProgress: (loaded, total) => {
                    setProgress(total > 0 ? loaded / total : 0);
                },
            });
            await reload();
        } catch (e) {
            setErr((e as Error).message || "upload failed");
        } finally {
            setUploading(null);
            setProgress(0);
            if (inputRef.current) inputRef.current.value = "";
        }
    }, [scope, reload]);

    const onDelete = useCallback(async (key: string) => {
        if (!confirm(`Delete ${key} from ${corpus.slug}? This can't be undone.`)) return;
        try {
            await viewerApi.adminDeleteBlob(scope, key);
            await reload();
        } catch (e) {
            setErr((e as Error).message || "delete failed");
        }
    }, [scope, corpus.slug, reload]);

    return (
        <div className="flex flex-col h-full">
            <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-3">
                <div className="text-xs text-gray-300 min-w-0">
                    <div className="font-mono truncate">{corpus.slug}</div>
                    {corpus.description && (
                        <div className="text-gray-500 truncate">{corpus.description}</div>
                    )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    <input
                        ref={inputRef}
                        type="file"
                        onChange={onPick}
                        className="hidden"
                        disabled={!!uploading}
                    />
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
            {err && (
                <div className="text-xs text-red-400 px-3 py-2">{err}</div>
            )}
            <div className="flex-1 min-h-0 overflow-auto">
                {files.length === 0 && !err && (
                    <div className="text-xs text-gray-500 italic px-3 py-4">
                        No files yet. Upload representative source files (STEP /
                        IFC / RMED / etc.) to drive regression sweeps from the
                        Audit Runs tab.
                    </div>
                )}
                {files.length > 0 && (
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
                                            onClick={() => onDelete(f.key)}
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
            </div>
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
                            <CorpusFiles corpus={selected}/>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default CorpusTab;
