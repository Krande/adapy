import React, {useCallback, useEffect, useMemo, useState} from "react";
import {AdminFileEntry, DerivedBlob, viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useConvertPageStore} from "@/state/convertPageStore";
import {view_in_3d} from "@/utils/scene/handlers/view_in_3d";
import {runtime} from "@/runtime/config";

// Pre-existing source-and-derived list for the /convert page. The
// upload widget above only shows files the user just dropped in this
// session; this panel surfaces everything already in the user's
// scope so they can grab a previously-converted GLB without
// re-running the converter (and without flipping over to the main
// viewer's storage browser). Refresh button hits the same endpoint
// rather than relying on a fragile push channel.

function fmtSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function isViewable(d: DerivedBlob): boolean {
    // Anything the main viewer can mount as a scene directly. The
    // streaming-FEA artefact tree is ``fea/<file>`` per the
    // _derived_source_of parser — a single mesh GLB inside that
    // tree is viewable via the streaming loader. Plain ``.glb`` /
    // ``.gltf`` outputs are obviously viewable.
    if (d.format === "glb" || d.format === "gltf") return true;
    if (d.format.startsWith("fea/") && d.key.endsWith(".glb")) return true;
    // Any derived product the converter can turn into GLB (IFC / XML /
    // STEP / SAT / …) is viewable too — overlay_file_in_scene converts the
    // derived blob to GLB on demand before mounting it. This lets you, e.g.,
    // visualize an IFC that was converted from a FEM or XML source.
    return runtime.conversionTargetsFor(d.format).includes("glb");
}

const DerivedRow: React.FC<{
    derived: DerivedBlob;
    sourceKey: string;
    scope: string;
}> = ({derived, sourceKey, scope}) => {
    const baseName = sourceKey.replace(/\.[^.]+$/, "");
    const ext = derived.key.slice(derived.key.lastIndexOf(".") + 1);

    const onDownload = useCallback(async () => {
        const suggested = `${baseName}.${ext}`;
        await viewerApi.downloadBlob(scope, derived.key, suggested);
    }, [scope, derived.key, baseName, ext]);

    const onViewIn3D = useCallback(() => {
        void view_in_3d(sourceKey, derived.key);
    }, [sourceKey, derived.key]);

    return (
        <div className="flex items-center gap-2 text-xs">
            <span className="font-mono text-gray-300 px-1.5 py-0.5 bg-gray-900 rounded-sm">
                .{derived.format}
            </span>
            <span className="text-gray-500">{fmtSize(derived.size)}</span>
            <div className="ml-auto flex items-center gap-1">
                <button
                    type="button"
                    onClick={onDownload}
                    className="bg-emerald-700/80 hover:bg-emerald-600 text-white text-xs px-2 py-0.5 rounded-sm"
                >
                    Download
                </button>
                {isViewable(derived) && (
                    <button
                        type="button"
                        onClick={onViewIn3D}
                        className="bg-gray-700 hover:bg-gray-600 text-gray-100 text-xs px-2 py-0.5 rounded-sm"
                    >
                        View in 3D ↗
                    </button>
                )}
            </div>
        </div>
    );
};

const ExistingSourceCard: React.FC<{
    entry: AdminFileEntry;
    scope: string;
}> = ({entry, scope}) => {
    const addRow = useConvertPageStore((s) => s.addRow);
    const rows = useConvertPageStore((s) => s.rows);
    const alreadyOnList = useMemo(
        () => rows.some((r) => r.sourceKey === entry.key),
        [rows, entry.key],
    );

    const onConvertAgain = useCallback(() => {
        // Pushes the source into the upload-row store as if the user
        // had just dropped it. ConversionRow re-reads target options
        // from the server, picks a sensible default, and surfaces
        // the Convert button — same UX as a fresh drop, no re-upload
        // needed since the source is already in storage.
        addRow({
            sourceKey: entry.key,
            sizeBytes: entry.size,
            addedAt: Date.now(),
            target: null,
        });
    }, [addRow, entry]);

    return (
        <div className="rounded-md border border-gray-700 bg-gray-800/40 p-3 space-y-2">
            <div className="flex justify-between items-start gap-3">
                <div className="min-w-0 flex-1">
                    <div className="font-mono text-sm truncate">{entry.key}</div>
                    <div className="text-[11px] text-gray-400">
                        {entry.format} · {fmtSize(entry.size)}
                        {entry.last_modified && (
                            <> · {new Date(entry.last_modified).toLocaleDateString()}</>
                        )}
                    </div>
                </div>
                {!alreadyOnList && entry.available_targets.length > 0 && (
                    <button
                        type="button"
                        onClick={onConvertAgain}
                        className="shrink-0 bg-blue-700 hover:bg-blue-600 text-white text-xs px-2 py-1 rounded-sm"
                        title="Convert this source to another format"
                    >
                        Convert…
                    </button>
                )}
            </div>
            {entry.derived.length > 0 ? (
                <div className="space-y-1 pl-2 border-l border-gray-700/60">
                    {entry.derived.map((d) => (
                        <DerivedRow
                            key={d.key}
                            derived={d}
                            sourceKey={entry.key}
                            scope={scope}
                        />
                    ))}
                </div>
            ) : (
                <div className="text-[11px] text-gray-500 italic pl-2">
                    no conversions yet
                </div>
            )}
        </div>
    );
};

const ExistingFilesPanel: React.FC = () => {
    const current = useScopeStore((s) => s.current);
    const [entries, setEntries] = useState<AdminFileEntry[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const scope = current ? scopeUrlPart(current) : null;

    const reload = useCallback(async () => {
        if (!scope) return;
        setLoading(true);
        setError(null);
        try {
            const list = await viewerApi.listFilesWithDerived(scope);
            setEntries(list);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, [scope]);

    useEffect(() => {
        void reload();
    }, [reload]);

    if (!scope) return null;

    return (
        <section className="space-y-2">
            <div className="flex items-center justify-between">
                <h2 className="text-xs uppercase tracking-wider text-gray-400">
                    Existing files in scope
                </h2>
                <button
                    type="button"
                    onClick={() => void reload()}
                    disabled={loading}
                    className="text-xs text-blue-400 hover:text-blue-300 disabled:opacity-50"
                >
                    {loading ? "Refreshing…" : "Refresh"}
                </button>
            </div>
            {error && (
                <div className="text-xs text-red-400 font-mono break-all">
                    {error}
                </div>
            )}
            {entries !== null && entries.length === 0 && !error && (
                <div className="text-xs text-gray-500 italic">
                    no files yet — drop something above to get started
                </div>
            )}
            {entries && entries.length > 0 && (
                <div className="space-y-2">
                    {entries.map((entry) => (
                        <ExistingSourceCard
                            key={entry.key}
                            entry={entry}
                            scope={scope}
                        />
                    ))}
                </div>
            )}
        </section>
    );
};

export default ExistingFilesPanel;
