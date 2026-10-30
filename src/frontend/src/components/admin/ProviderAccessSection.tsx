import React, {useCallback, useEffect, useMemo, useState} from "react";

import {
    SHARED_TARGET,
    collectionLabel,
    grantInput,
    projectTarget,
    targetLabel,
    type ProviderGrant,
} from "@/assets/providerGrants";
import type {AdminProject, AssetSchedulesListing} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {useProviderName} from "@/state/providerNamesStore";
import {sortProjects, sortProjectsBy} from "@/utils/sortProjects";

import AdminSection from "./AdminSection";

// Admin → Providers → Access: which scopes may read which of the provider store's datasets.
//
// Provider data is stored once, in the provider store, and no scope holds a copy. A GRANT maps a
// scope -- Shared, or a project -- to a dataset, a provider's collection or all of the provider's
// collections, and says what the scope may read of it: its ATTRIBUTES (search, values, export) and/or
// its GEOMETRY (tree, delivery, builds, design geometry in an export). Members read through the
// grant; nothing is copied into their scope, and revoking takes effect on their next request.
//
// There is no "request" permission here, on purpose: asking a provider for data runs a job in the
// store, and every such job is an admin operation. Rows saved from this tab always send it off.
//
// The providers and their collections are what the schedules endpoint reports -- the live workers'
// declarations -- but every stored grant is listed whether or not a worker advertises it now:
// offline is not revocation.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "—");

const ALL = "";
const select = "bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5 text-xs";

const rowKey = (g: Pick<ProviderGrant, "target_scope" | "provider" | "collection">) =>
    `${g.target_scope}\u0000${g.provider}\u0000${g.collection ?? ""}`;

/** The note, saved on Enter or leaving the field; Escape puts it back. */
const NoteField: React.FC<{value: string; disabled: boolean; onSave: (next: string) => void}> = ({value, disabled, onSave}) => {
    const [draft, setDraft] = useState(value);
    useEffect(() => setDraft(value), [value]);
    return (
        <input
            type="text"
            value={draft}
            disabled={disabled}
            placeholder="note"
            aria-label="Note"
            className="w-44 rounded-sm border border-gray-700 bg-gray-800 px-2 py-0.5 text-xs text-gray-100 placeholder:text-gray-500"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => draft.trim() !== value.trim() && onSave(draft)}
            onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
                else if (e.key === "Escape") {
                    setDraft(value);
                    requestAnimationFrame(() => (e.target as HTMLInputElement).blur());
                }
            }}
        />
    );
};

const GrantRow: React.FC<{
    grant: ProviderGrant;
    projectNames: ReadonlyMap<string, string>;
    onChanged: () => void;
}> = ({grant: g, projectNames, onChanged}) => {
    const pn = useProviderName();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const act = async (fn: () => Promise<unknown>) => {
        setBusy(true);
        setError(null);
        try {
            await fn();
            onChanged();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };
    const save = (patch: Parameters<typeof grantInput>[1]) =>
        act(() => viewerApi.adminSetProviderGrant(grantInput(g, patch)));

    return (
        <tr className="border-t border-gray-800 align-top">
            <td className="px-2 py-1.5">{targetLabel(g.target_scope, projectNames)}</td>
            <td className="px-2 py-1.5">
                <span title={g.provider}>{pn(g.provider)}</span>
            </td>
            <td className="px-2 py-1.5 font-mono">{collectionLabel(g.collection)}</td>
            <td className="px-2 py-1.5">
                <input
                    type="checkbox"
                    checked={g.attributes}
                    disabled={busy}
                    aria-label="Attributes"
                    onChange={(e) => void save({attributes: e.target.checked})}
                />
            </td>
            <td className="px-2 py-1.5">
                <input
                    type="checkbox"
                    checked={g.geometry}
                    disabled={busy}
                    aria-label="Geometry"
                    onChange={(e) => void save({geometry: e.target.checked})}
                />
            </td>
            <td className="px-2 py-1.5">
                <NoteField value={g.note ?? ""} disabled={busy} onSave={(note) => void save({note})} />
                <div className="text-[11px] text-gray-500 mt-0.5">
                    {g.granted_by ? `${g.granted_by} · ` : ""}
                    {fmt(g.granted_at)}
                </div>
            </td>
            <td className="px-2 py-1.5 whitespace-nowrap">
                <button
                    type="button"
                    className="bg-red-800 hover:bg-red-700 text-white px-1.5 py-0.5 rounded-sm disabled:opacity-50"
                    disabled={busy}
                    title="Takes effect on the scope's next request; nothing it already exported is removed"
                    onClick={() => {
                        const what = `${pn(g.provider)} / ${collectionLabel(g.collection)}`;
                        if (confirm(`Revoke ${targetLabel(g.target_scope, projectNames)}'s access to ${what}?`)) {
                            void act(() =>
                                viewerApi.adminRevokeProviderGrant({
                                    target_scope: g.target_scope,
                                    provider: g.provider,
                                    collection: g.collection,
                                }),
                            );
                        }
                    }}
                >
                    Revoke
                </button>
                {error && <div className="text-[11px] text-red-300 mt-1 whitespace-normal">{error}</div>}
            </td>
        </tr>
    );
};

const AddGrant: React.FC<{
    providers: AssetSchedulesListing["providers"];
    projects: readonly AdminProject[];
    onAdded: () => void;
}> = ({providers, projects, onAdded}) => {
    const pn = useProviderName();
    const live = useMemo(() => projects.filter((p) => !p.archived_at), [projects]);
    const targets = useMemo(
        () => [
            {value: SHARED_TARGET, label: "Shared"},
            ...sortProjectsBy(live, (p) => p.name).map((p) => ({value: projectTarget(p.id), label: `Project · ${p.name}`})),
        ],
        [live],
    );
    const [target, setTarget] = useState(SHARED_TARGET);
    const [providerId, setProviderId] = useState(providers[0]?.provider ?? "");
    const [collection, setCollection] = useState(ALL);
    const [attributes, setAttributes] = useState(true);
    const [geometry, setGeometry] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        if (!providerId && providers[0]) setProviderId(providers[0].provider);
    }, [providers, providerId]);

    const collections = useMemo(
        () => sortProjects(providers.find((p) => p.provider === providerId)?.collections ?? []),
        [providers, providerId],
    );

    const add = async () => {
        setBusy(true);
        setError(null);
        try {
            await viewerApi.adminSetProviderGrant(
                grantInput({target_scope: target, provider: providerId, collection: collection || null, attributes, geometry}),
            );
            setCollection(ALL);
            onAdded();
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="mx-3 my-2 rounded-sm border border-gray-800 bg-gray-900/40 p-2 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <select className={select} value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Scope">
                    {targets.map((t) => (
                        <option key={t.value} value={t.value}>
                            {t.label}
                        </option>
                    ))}
                </select>
                <select
                    className={select}
                    value={providerId}
                    onChange={(e) => {
                        setProviderId(e.target.value);
                        setCollection(ALL);
                    }}
                    aria-label="Provider"
                >
                    {providers.map((p) => (
                        <option key={p.provider} value={p.provider} title={p.provider}>
                            {pn(p.provider)}
                        </option>
                    ))}
                </select>
                <select className={select} value={collection} onChange={(e) => setCollection(e.target.value)} aria-label="Collection">
                    <option value={ALL}>All collections</option>
                    {collections.map((c) => (
                        <option key={c} value={c}>
                            {c}
                        </option>
                    ))}
                </select>
                <label className="inline-flex items-center gap-1">
                    <input type="checkbox" checked={attributes} onChange={(e) => setAttributes(e.target.checked)} />
                    attributes
                </label>
                <label className="inline-flex items-center gap-1">
                    <input type="checkbox" checked={geometry} onChange={(e) => setGeometry(e.target.checked)} />
                    geometry
                </label>
                <button
                    type="button"
                    className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm"
                    disabled={busy || !providerId || (!attributes && !geometry)}
                    onClick={() => void add()}
                >
                    {busy ? "…" : "Add grant"}
                </button>
            </div>
            {error && <div className="text-xs text-red-300">{error}</div>}
        </div>
    );
};

const ProviderAccessSection: React.FC<{projects: readonly AdminProject[]}> = ({projects}) => {
    const [grants, setGrants] = useState<ProviderGrant[] | null>(null);
    const [providers, setProviders] = useState<AssetSchedulesListing["providers"]>([]);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const [list, schedules] = await Promise.all([
                viewerApi.adminProviderGrants(),
                // Only for the providers and their collections; a failure leaves the list of grants usable.
                viewerApi.adminAssetSchedules().catch(() => null),
            ]);
            setGrants(list);
            if (schedules) setProviders(schedules.providers);
            setError(null);
        } catch (e) {
            setError(errText(e));
        }
    }, []);
    useEffect(() => {
        void load();
    }, [load]);

    const projectNames = useMemo(() => new Map(projects.map((p) => [p.id, p.name])), [projects]);
    const sorted = useMemo(
        () =>
            [...(grants ?? [])].sort(
                (a, b) =>
                    targetLabel(a.target_scope, projectNames).localeCompare(targetLabel(b.target_scope, projectNames)) ||
                    a.provider.localeCompare(b.provider) ||
                    (a.collection ?? "").localeCompare(b.collection ?? ""),
            ),
        [grants, projectNames],
    );

    return (
        <AdminSection
            title="Access"
            kind="Grants"
            storageKey="ada.admin.providers.open.access"
            testId="provider-access-admin"
            actions={
                <button
                    type="button"
                    className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800"
                    onClick={() => void load()}
                >
                    Refresh
                </button>
            }
        >
            <div className="px-3 pt-2 pb-1 text-xs text-gray-400">
                Provider data is stored once, in the provider store. A grant lets a scope read a provider's collection (or
                all of its collections) from there: its attributes, its geometry, or both. Nothing is copied into the scope,
                and a revoke applies on its next request. Asking a provider for data is always an admin operation.
            </div>
            {error && <div className="px-3 py-1 text-xs text-red-300">{error}</div>}
            {grants && grants.length === 0 && <div className="px-3 py-2 text-xs text-gray-500">No scope has a grant.</div>}
            {sorted.length > 0 && (
                <div className="overflow-x-auto px-3">
                    <table className="w-full text-xs">
                        <thead className="text-left text-gray-500">
                            <tr>
                                <th className="px-2 py-1 font-normal">Scope</th>
                                <th className="px-2 py-1 font-normal">Provider</th>
                                <th className="px-2 py-1 font-normal">Collection</th>
                                <th className="px-2 py-1 font-normal">Attributes</th>
                                <th className="px-2 py-1 font-normal">Geometry</th>
                                <th className="px-2 py-1 font-normal">Note</th>
                                <th className="px-2 py-1 font-normal" />
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map((g) => (
                                <GrantRow key={rowKey(g)} grant={g} projectNames={projectNames} onChanged={() => void load()} />
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {providers.length > 0 ? (
                <AddGrant providers={providers} projects={projects} onAdded={() => void load()} />
            ) : (
                <div className="px-3 py-2 text-xs text-gray-500">
                    No online provider is advertised right now, so a grant cannot be added; the ones above stay in force.
                </div>
            )}
        </AdminSection>
    );
};

export default ProviderAccessSection;
