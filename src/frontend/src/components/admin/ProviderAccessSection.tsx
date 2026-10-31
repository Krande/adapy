import React, {useCallback, useEffect, useMemo, useState} from "react";

import {
    SHARED_TARGET,
    collectionLabel,
    grantChoices,
    grantDelta,
    grantInput,
    projectTarget,
    scopeProviderAccess,
    targetLabel,
    userTarget,
    type GrantChoice,
    type ProviderGrant,
} from "@/assets/providerGrants";
import {fuzzyFilter} from "@/services/fuzzy";
import type {AdminProject, AssetSchedulesListing} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {useMeStore} from "@/state/meStore";
import {useProviderName} from "@/state/providerNamesStore";
import {sortProjects, sortProjectsBy} from "@/utils/sortProjects";

import AdminSection from "./AdminSection";

// Admin → Providers → Access: which projects (a provider's collections) each scope may read. THE
// only control of it -- the older per-scope collection matrix is migrated into these grants at
// startup and no longer read.
//
// Provider data is stored once, in the provider store, and no scope holds a copy. Per SCOPE -- Shared,
// your own personal scope, each project -- and per provider, one checkbox per collection: ticked, the
// scope reads that collection (its attributes and its geometry); a scope may read many. "All
// collections" is a provider-wide grant that also covers collections published later. Members read
// through the grant; nothing is copied into their scope, and a revoke applies on their next request.
//
// There is no "request" permission here, on purpose: asking a provider for data runs a job in the
// store, and every such job is an admin operation.
//
// The collections are what the live workers declare (the schedules endpoint), plus every granted one
// no worker advertises right now -- offline is not revocation. "Every grant" below lists the stored
// rows themselves, for the rare grant of only attributes or only geometry, and for notes.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "—");

const rowKey = (g: Pick<ProviderGrant, "target_scope" | "provider" | "collection">) =>
    `${g.target_scope}\u0000${g.provider}\u0000${g.collection ?? ""}`;

interface ScopeTarget {
    target: string;
    label: string;
    hint: string;
}

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

/** One scope's checkboxes for one provider. */
const ProviderGrants: React.FC<{
    scope: ScopeTarget;
    provider: string;
    advertised: readonly string[];
    grants: readonly ProviderGrant[];
    onGrants: (target: string, provider: string, next: ProviderGrant[] | null) => void;
}> = ({scope, provider, advertised, grants, onGrants}) => {
    const pn = useProviderName();
    const [filter, setFilter] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const access = useMemo(() => scopeProviderAccess(grants, scope.target, provider), [grants, scope.target, provider]);
    const choices = useMemo(() => {
        const all = grantChoices(advertised, access);
        const order = sortProjects(all.map((c) => c.collection));
        const at = new Map(order.map((c, i) => [c, i]));
        return [...all].sort((a, b) => (at.get(a.collection) ?? 0) - (at.get(b.collection) ?? 0));
    }, [advertised, access]);
    const shown = fuzzyFilter(choices, filter, (c: GrantChoice) => c.collection);
    const granted = choices.filter((c) => c.granted).length;

    const run = async (fn: () => Promise<ProviderGrant[] | null>) => {
        setBusy(true);
        setError(null);
        try {
            onGrants(scope.target, provider, await fn());
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };
    const setTicks = (collections: readonly string[], on: boolean) => {
        const body = grantDelta(choices, collections, on);
        if (!body.grant.length && !body.revoke.length) return;
        void run(async () => {
            const out = await viewerApi.adminSetProviderGrantCollections({target_scope: scope.target, provider, ...body});
            return out.grants;
        });
    };
    const setAll = (on: boolean) =>
        void run(async () => {
            if (on) {
                await viewerApi.adminSetProviderGrant(grantInput({target_scope: scope.target, provider, collection: null}));
            } else if (confirm(`Revoke ${scope.label}'s access to every ${pn(provider)} collection?`)) {
                await viewerApi.adminRevokeProviderGrant({target_scope: scope.target, provider, collection: null});
            } else {
                return grants.filter((g) => g.target_scope === scope.target && g.provider === provider);
            }
            return null; // re-read: the provider-wide row is not in a batch answer
        });
    const filtered = filter.trim() ? " shown" : "";

    return (
        <div className="rounded-sm border border-gray-800 bg-gray-900/40 p-2 space-y-1.5">
            <div className="flex flex-wrap items-center gap-3 text-xs">
                <span className="font-medium text-gray-200" title={provider}>
                    {pn(provider)}
                </span>
                <span className="text-gray-500">
                    {access.all ? "every collection" : `${granted} of ${choices.length}`}
                </span>
                <label className="inline-flex items-center gap-1" title="A provider-wide grant: every collection, the ones published later included">
                    <input type="checkbox" checked={access.all} disabled={busy} onChange={(e) => setAll(e.target.checked)} />
                    All collections
                </label>
            </div>
            {!access.all && (
                <>
                    <div className="flex flex-wrap items-center gap-2">
                        <input
                            type="search"
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            placeholder={`Filter ${choices.length} collections…`}
                            aria-label={`Filter ${pn(provider)} collections for ${scope.label}`}
                            className="w-56 rounded-sm border border-gray-700 bg-gray-800 px-2 py-0.5 text-xs text-gray-100 placeholder:text-gray-500"
                        />
                        <button
                            type="button"
                            className="text-xs px-2 py-0.5 rounded-sm border border-gray-700 hover:bg-gray-800 disabled:opacity-50"
                            disabled={busy || shown.every((c) => c.granted)}
                            onClick={() => setTicks(shown.map((c) => c.collection), true)}
                        >
                            Select all{filtered}
                        </button>
                        <button
                            type="button"
                            className="text-xs px-2 py-0.5 rounded-sm border border-gray-700 hover:bg-gray-800 disabled:opacity-50"
                            disabled={busy || shown.every((c) => !c.granted)}
                            onClick={() => setTicks(shown.map((c) => c.collection), false)}
                        >
                            Deselect all{filtered}
                        </button>
                    </div>
                    {choices.length === 0 && (
                        <div className="text-[11px] text-gray-500">Nothing is advertised and nothing is granted.</div>
                    )}
                    <ul className="max-h-64 overflow-auto grid grid-cols-2 sm:grid-cols-4 gap-x-2">
                        {shown.map((c) => (
                            <li key={c.collection}>
                                <label
                                    className="flex items-center gap-2 py-0.5 px-1 text-xs rounded-sm cursor-pointer hover:bg-gray-800/60"
                                    title={c.advertised ? c.collection : "Granted, but no online worker advertises it right now. Kept until unticked."}
                                >
                                    <input
                                        type="checkbox"
                                        checked={c.granted}
                                        disabled={busy}
                                        aria-label={`${scope.label} reads ${c.collection}`}
                                        onChange={(e) => setTicks([c.collection], e.target.checked)}
                                    />
                                    <span className="truncate text-gray-200">{c.collection}</span>
                                    {!c.advertised && <span className="text-[10px] text-amber-400">offline</span>}
                                </label>
                            </li>
                        ))}
                    </ul>
                </>
            )}
            {error && <div className="text-xs text-red-300">{error}</div>}
        </div>
    );
};

const ProviderAccessSection: React.FC<{projects: readonly AdminProject[]}> = ({projects}) => {
    const [grants, setGrants] = useState<ProviderGrant[] | null>(null);
    const [providers, setProviders] = useState<AssetSchedulesListing["providers"]>([]);
    const [error, setError] = useState<string | null>(null);
    const [scopeFilter, setScopeFilter] = useState("");
    const mySub = useMeStore((s) => s.sub);
    const myName = useMeStore((s) => s.displayName || s.email);
    const pn = useProviderName();

    const load = useCallback(async () => {
        try {
            const [list, schedules] = await Promise.all([
                viewerApi.adminProviderGrants(),
                // Only for the providers and their collections; a failure leaves the grants usable.
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

    /** Replace one scope's grants of one provider with `next` (null: read everything again). */
    const onGrants = useCallback(
        (target: string, provider: string, next: ProviderGrant[] | null) => {
            if (next === null) {
                void load();
                return;
            }
            setGrants((cur) => [...(cur ?? []).filter((g) => !(g.target_scope === target && g.provider === provider)), ...next]);
        },
        [load],
    );

    const projectNames = useMemo(() => new Map(projects.map((p) => [p.id, p.name])), [projects]);
    const scopes: ScopeTarget[] = useMemo(() => {
        const out: ScopeTarget[] = [{target: SHARED_TARGET, label: "Shared", hint: "everyone with access to this viewer"}];
        if (mySub) out.push({target: userTarget(mySub), label: "Your personal scope", hint: `${myName || mySub} only`});
        for (const p of sortProjectsBy(
            projects.filter((x) => !x.archived_at),
            (x) => x.name,
        )) {
            out.push({target: projectTarget(p.id), label: p.name, hint: p.slug});
        }
        return out;
    }, [projects, mySub, myName]);
    // Every provider a worker declares, plus any a stored grant names that none declares now.
    const providerIds = useMemo(() => {
        const ids = new Set([...providers.map((p) => p.provider), ...(grants ?? []).map((g) => g.provider)]);
        return [...ids].sort();
    }, [providers, grants]);
    const advertisedOf = useMemo(() => new Map(providers.map((p) => [p.provider, p.collections ?? []])), [providers]);
    const shownScopes = fuzzyFilter(scopes, scopeFilter, (s) => `${s.label} ${s.hint}`);
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

    const summary = (target: string) =>
        providerIds
            .map((p) => {
                const a = scopeProviderAccess(grants ?? [], target, p);
                if (a.all) return `${pn(p)}: all`;
                return a.collections.size ? `${pn(p)}: ${a.collections.size}` : null;
            })
            .filter(Boolean)
            .join(" · ") || "nothing granted";

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
            <div className="px-3 pt-2 pb-1 text-xs text-gray-400 space-y-1">
                <p>
                    Which projects each scope may read, per provider. Ticked, the scope reads that collection's attributes and
                    geometry from the provider store; a scope may read many. “All collections” also covers ones published later.
                </p>
                <p>
                    Nothing is copied into the scope, and a revoke applies on its next request. Keeping provider data current is
                    the scheduled jobs' work, always an admin's.
                </p>
            </div>
            {error && <div className="px-3 py-1 text-xs text-red-300">{error}</div>}
            {grants !== null && providerIds.length === 0 && (
                <div className="px-3 py-2 text-xs text-gray-500">
                    No online provider is advertised right now and nothing is granted.
                </div>
            )}
            {grants !== null && providerIds.length > 0 && (
                <div className="px-3 py-1 space-y-1.5">
                    {scopes.length > 6 && (
                        <input
                            type="search"
                            value={scopeFilter}
                            onChange={(e) => setScopeFilter(e.target.value)}
                            placeholder={`Filter ${scopes.length} scopes…`}
                            aria-label="Filter scopes"
                            className="w-64 rounded-sm border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-100 placeholder:text-gray-500"
                        />
                    )}
                    {shownScopes.map((scope) => (
                        <details key={scope.target} className="rounded-sm border border-gray-800" data-testid={`access-scope-${scope.target}`}>
                            <summary className="cursor-pointer select-none px-2 py-1.5 text-xs flex flex-wrap items-baseline gap-2">
                                <span className="font-medium text-gray-100">{scope.label}</span>
                                <span className="text-gray-500">{scope.hint}</span>
                                <span className="ml-auto text-gray-400">{summary(scope.target)}</span>
                            </summary>
                            <div className="px-2 pb-2 space-y-1.5">
                                {providerIds.map((provider) => (
                                    <ProviderGrants
                                        key={provider}
                                        scope={scope}
                                        provider={provider}
                                        advertised={advertisedOf.get(provider) ?? []}
                                        grants={grants}
                                        onGrants={onGrants}
                                    />
                                ))}
                            </div>
                        </details>
                    ))}
                </div>
            )}
            {sorted.length > 0 && (
                <details className="px-3 py-2">
                    <summary className="cursor-pointer select-none text-xs text-gray-400">
                        Every grant ({sorted.length}) — attributes or geometry only, notes
                    </summary>
                    <div className="overflow-x-auto mt-1">
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
                </details>
            )}
        </AdminSection>
    );
};

export default ProviderAccessSection;
