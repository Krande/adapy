// "Files": the SOURCES in this scope, one entry each, with what was derived from them.
//
// What a person manages is sources -- an exported model file, a provider's project tree -- not the
// hierarchies, attributes, manifests and cached builds made from them. So each publish is one row,
// named by its source (the stored file's name, or `tree-<provider>`), with its derived files listed underneath for
// reference only. Deleting a source removes everything derived from it, in the order the server
// chooses (manifests first); it is refused, naming the holder, while another source still references
// one of its files.
//
// Staged files are in the Staged section above. A derived file whose source is already gone is an
// orphan: listed separately, so it can be removed.

import React, { useCallback, useState } from "react";

import { assetSourcesApi, assetsApi, type WireAssetSource } from "@/services/api/assets";
import { providerIdTitle } from "@/assets/providerNames";
import type { ScopeUrl } from "@/services/api/client";
import { providerName, useProviderName } from "@/state/providerNamesStore";

import { formatRevision } from "./format";

export function formatSize(n: number | null | undefined): string {
    if (n == null) return "";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} kB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const ROW_BTN = "shrink-0 h-5 w-5 grid place-items-center rounded text-gray-500 hover:text-red-300 hover:bg-white/5 disabled:opacity-40";

/** The small "delete" glyph a row ends with. */
export const DeleteButton: React.FC<{ title: string; disabled?: boolean; onClick: () => void }> = ({ title, disabled, onClick }) => (
    <button type="button" className={ROW_BTN} title={title} aria-label={title} disabled={disabled} onClick={onClick}>
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M3 4.5h10M6.5 4.5V3h3v1.5M5 4.5l.6 8.5h4.8l.6-8.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    </button>
);

const sourceKey = (s: Pick<WireAssetSource, "collection" | "revision" | "provider">) => `${s.provider}|${s.collection}|${s.revision}`;

const AssetFiles: React.FC<{
    scope: string;
    /** Called after anything was deleted, so the tree re-reads what is published. */
    onChanged: () => void;
}> = ({ scope, onChanged }) => {
    const [data, setData] = useState<{ sources: WireAssetSource[]; orphans: { key: string; size: number }[] } | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [filter, setFilter] = useState("");

    const load = useCallback(async () => {
        setError(null);
        try {
            setData(await assetSourcesApi.list(scope as ScopeUrl));
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    }, [scope]);

    const q = filter.trim().toLowerCase();
    const sources = (data?.sources ?? []).filter(
        (s) => !q || `${s.label} ${s.collection} ${s.provider} ${providerName(s.provider)} ${s.revision}`.toLowerCase().includes(q),
    );

    const act = async (label: string, fn: () => Promise<string | null>) => {
        setBusy(label);
        setError(null);
        try {
            const refused = await fn();
            if (refused) setError(refused);
            else onChanged();
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
            await load();
        }
    };

    const removeSource = (s: WireAssetSource) => {
        const what = `${s.label} (${providerName(s.provider)}, ${s.collection} @ ${formatRevision(s.revision)})`;
        if (
            !window.confirm(
                `Delete ${what}?\n\nEverything derived from it goes with it: ${s.derived_files} file(s), ` +
                    `${formatSize(s.derived_size)} -- the ${s.subjects} node(s) it published and their cached builds.`,
            )
        )
            return;
        void act(sourceKey(s), async () => {
            const out = await assetSourcesApi.remove(scope as ScopeUrl, s);
            return out.ok ? null : `Not deleted: ${out.reason ?? "refused"}`;
        });
    };

    const removeOrphan = (key: string) => {
        if (!window.confirm(`Delete ${key}?\n\nIts source is already gone.`)) return;
        void act(key, async () => {
            await assetsApi.deleteFile(scope as ScopeUrl, key);
            return null;
        });
    };

    return (
        <details
            onToggle={(e) => {
                if ((e.currentTarget as HTMLDetailsElement).open && !data) void load();
            }}
        >
            <summary className="cursor-pointer text-gray-400">
                Files
                {data && (
                    <span className="text-gray-500">
                        {" "}
                        ({data.sources.length} source{data.sources.length === 1 ? "" : "s"} ·{" "}
                        {formatSize(data.sources.reduce((n, s) => n + s.derived_size, 0))})
                    </span>
                )}
            </summary>
            <div className="mt-1 space-y-2">
                <div className="flex items-center gap-2">
                    <input
                        aria-label="Filter sources"
                        className="flex-1 min-w-0 h-6 rounded border border-gray-700 bg-gray-900/60 px-2 text-gray-100 placeholder:text-gray-500 outline-none"
                        placeholder="Filter by name, collection, provider"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                    />
                    <button type="button" className="h-6 px-2 rounded text-gray-400 hover:text-white" disabled={!!busy} onClick={() => void load()}>
                        Refresh
                    </button>
                </div>
                {error && <div className="text-red-300 break-words">{error}</div>}
                {!data && !error && <div className="text-gray-500">Reading sources…</div>}
                {data && (
                    <>
                        {sources.length === 0 && <div className="pl-1 text-gray-500">No sources.</div>}
                        <ul className="space-y-0.5">
                            {sources.map((s) => (
                                <SourceRow
                                    key={sourceKey(s)}
                                    scope={scope}
                                    source={s}
                                    busy={busy === sourceKey(s)}
                                    disabled={!!busy}
                                    onDelete={() => removeSource(s)}
                                />
                            ))}
                        </ul>
                        {data.orphans.length > 0 && (
                            <details>
                                <summary className="cursor-pointer text-amber-300">
                                    Derived files without a source ({data.orphans.length})
                                </summary>
                                <ul className="pl-3">
                                    {data.orphans.map((o) => (
                                        <li key={o.key} className="flex items-center gap-2 px-1 rounded hover:bg-white/5">
                                            <span className="truncate text-gray-300" title={o.key}>
                                                {o.key}
                                            </span>
                                            <span className="ml-auto shrink-0 font-mono text-[11px] text-gray-500">{formatSize(o.size)}</span>
                                            {busy === o.key ? (
                                                <span className="text-gray-400">…</span>
                                            ) : (
                                                <DeleteButton title="Delete this orphaned file" disabled={!!busy} onClick={() => removeOrphan(o.key)} />
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            </details>
                        )}
                    </>
                )}
            </div>
        </details>
    );
};

/** One source; its derived files are fetched when it is opened, for reference. */
const SourceRow: React.FC<{
    scope: string;
    source: WireAssetSource;
    busy: boolean;
    disabled: boolean;
    onDelete: () => void;
}> = ({ scope, source, busy, disabled, onDelete }) => {
    const pn = useProviderName();
    const [files, setFiles] = useState<{ key: string; size: number }[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    return (
        <li>
            <details
                onToggle={(e) => {
                    if (!(e.currentTarget as HTMLDetailsElement).open || files) return;
                    void assetSourcesApi
                        .files(scope as ScopeUrl, source)
                        .then((d) => setFiles([...d.published, ...d.derived]))
                        .catch((err) => setError(err instanceof Error ? err.message : String(err)));
                }}
            >
                <summary className="flex items-center gap-2 cursor-pointer rounded px-1 hover:bg-white/5">
                    <span
                        className="truncate font-medium text-gray-100"
                        title={`${pn(source.provider)} · ${source.collection} @ ${source.revision}\n${providerIdTitle(source.provider)}`}
                    >
                        {source.label}
                    </span>
                    <span className="truncate text-gray-500">
                        {source.collection} · {pn(source.provider)} · {formatRevision(source.revision)}
                    </span>
                    <span
                        className="ml-auto shrink-0 font-mono text-[11px] text-gray-500"
                        title={`${source.derived_files} derived file(s) across ${source.subjects} node(s)`}
                    >
                        {formatSize(source.derived_size)}
                    </span>
                    {busy ? <span className="text-gray-400">…</span> : <DeleteButton title="Delete this source and everything derived from it" disabled={disabled} onClick={onDelete} />}
                </summary>
                <div className="pl-3">
                    {error && <div className="text-red-300 break-words">{error}</div>}
                    {!files && !error && <div className="text-gray-500">Reading…</div>}
                    {files && (
                        <ul title="Derived from this source; deleted with it">
                            {files.map((f) => (
                                <li key={f.key} className="flex items-center gap-2 px-1 text-gray-400">
                                    <span className="truncate" title={f.key}>
                                        {f.key}
                                    </span>
                                    <span className="ml-auto shrink-0 font-mono text-[11px] text-gray-500">{formatSize(f.size)}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </details>
        </li>
    );
};

export default AssetFiles;
