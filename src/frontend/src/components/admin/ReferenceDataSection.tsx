import React, {useCallback, useEffect, useMemo, useState} from "react";

import {PROVIDER_STORE_SCOPE} from "@/assets/providerStore";
import {
    referenceDataApi,
    type ReferenceItem,
    type ReferencePage,
    type ReferenceSet,
} from "@/services/api/referenceData";
import {ApiError} from "@/services/viewerApi";
import {useProviderName} from "@/state/providerNamesStore";

import AdminSection from "./AdminSection";

// Admin → Providers → Reference data: what each collection OFFERS for a new element to point at --
// every spec, spec component and material its provider listed, used by the model or not. What a
// macro writes after SPRE or MATR is the `ref` column, verbatim, with a copy button.
//
// Read from the provider store (where provider data lives); a scope reads the same sets through its
// attributes grant (`GET /scopes/{scope}/assets/reference`). "Used by" is counted from the
// collection's attribute index for the rows on the page, so an unused entry shows 0, not nothing.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const select = "bg-gray-800 border border-gray-700 rounded-sm px-1.5 py-0.5 text-xs";
const PAGE = 200;
const USE_LABELS: Record<string, string> = {
    section: "Sections (GENSEC / SCTN)",
    plate: "Plates (PANEL)",
    fitting: "Fittings",
    joint: "Joints",
    material: "Materials",
    other: "Other",
};

const setKey = (s: Pick<ReferenceSet, "provider" | "collection">) => `${s.provider}\u0000${s.collection}`;

/** What the row says beyond its ref, by kind: a section's GTYP and p-lines, its parameters, a
 *  material's density. */
function detail(item: ReferenceItem): string {
    const a = item.attrs as Record<string, unknown>;
    const parts: string[] = [];
    if (typeof a.gtype === "string") parts.push(a.gtype);
    if (Array.isArray(a.params)) {
        const shown = (a.params as Array<Record<string, unknown>>)
            .filter((p) => p && p.key)
            .map((p) => (p.value !== undefined ? `${p.key}=${p.value}` : String(p.key)));
        if (shown.length) parts.push(shown.slice(0, 6).join(" ") + (shown.length > 6 ? " …" : ""));
    }
    if (Array.isArray(a.plines) && a.plines.length) parts.push(`JUSL: ${(a.plines as string[]).join(", ")}`);
    if (typeof a.density === "number") parts.push(`${a.density} kg/m³`);
    const desc = a.description ?? a.spec_description;
    if (typeof desc === "string" && desc) parts.push(desc);
    return parts.join(" · ");
}

function usedText(item: ReferenceItem): string {
    if (item.used === undefined || item.used === null) return "—";
    const kinds = Object.entries(item.used.by_kind).map(([k, n]) => `${n} ${k}`);
    return item.used.count === 0 ? "unused" : kinds.join(", ");
}

const SetLabel: React.FC<{set: ReferenceSet}> = ({set}) => {
    const nameOf = useProviderName();
    return <>{`${set.collection} — ${nameOf(set.provider)}`}</>;
};

const ReferenceDataSection: React.FC = () => {
    const [sets, setSets] = useState<ReferenceSet[]>([]);
    const [chosen, setChosen] = useState<string>("");
    const [use, setUse] = useState<string>("");
    const [kind, setKind] = useState<string>("");
    const [q, setQ] = useState<string>("");
    const [usage, setUsage] = useState(true);
    const [page, setPage] = useState<ReferencePage | null>(null);
    const [items, setItems] = useState<ReferenceItem[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [copied, setCopied] = useState<string | null>(null);

    useEffect(() => {
        referenceDataApi
            .sets(PROVIDER_STORE_SCOPE)
            .then((s) => {
                setSets(s);
                setChosen((c) => c || (s[0] ? setKey(s[0]) : ""));
            })
            .catch((e) => setError(errText(e)));
    }, []);

    const current = useMemo(() => sets.find((s) => setKey(s) === chosen) ?? null, [sets, chosen]);

    const load = useCallback(
        async (offset: number, signal?: AbortSignal) => {
            if (!current) return;
            setLoading(true);
            try {
                const res = await referenceDataApi.items(
                    PROVIDER_STORE_SCOPE,
                    {
                        provider: current.provider,
                        collection: current.collection,
                        use: use || undefined,
                        kind: kind || undefined,
                        q: q.trim() || undefined,
                        usage,
                        limit: PAGE,
                        offset,
                    },
                    signal,
                );
                setPage(res);
                setItems((prev) => (offset === 0 ? [...res.items] : [...prev, ...res.items]));
                setError(null);
            } catch (e) {
                if (!(e instanceof DOMException && e.name === "AbortError")) setError(errText(e));
            } finally {
                setLoading(false);
            }
        },
        [current, use, kind, q, usage],
    );

    // A new filter starts from the first page; typing waits for a pause.
    useEffect(() => {
        const ctl = new AbortController();
        const t = window.setTimeout(() => void load(0, ctl.signal), 250);
        return () => {
            window.clearTimeout(t);
            ctl.abort();
        };
    }, [load]);

    const copy = (ref: string) => {
        void navigator.clipboard?.writeText(ref).then(() => {
            setCopied(ref);
            window.setTimeout(() => setCopied((c) => (c === ref ? null : c)), 1200);
        });
    };

    const uses = page?.facets.uses ?? {};
    return (
        <AdminSection
            title="Reference data"
            kind="Catalogue"
            subtitle="What each collection offers for a new element to point at — specs, spec components (SPRE) and materials (MATR), used or not."
            storageKey="admin.providers.reference.open"
            defaultOpen={false}
            testId="reference-data-section"
        >
            <div className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs text-gray-300">
                {sets.length === 0 ? (
                    <span className="text-gray-500">
                        No reference data yet: run a provider&apos;s export with “Catalogue &amp; reference data” ticked.
                    </span>
                ) : (
                    <>
                        <select className={select} value={chosen} aria-label="Collection" onChange={(e) => setChosen(e.target.value)}>
                            {sets.map((s) => (
                                <option key={setKey(s)} value={setKey(s)}>
                                    {s.collection} — {s.provider}
                                </option>
                            ))}
                        </select>
                        <select className={select} value={use} aria-label="For" onChange={(e) => setUse(e.target.value)}>
                            <option value="">Everything</option>
                            {Object.keys(USE_LABELS).map((u) => (
                                <option key={u} value={u}>
                                    {USE_LABELS[u]}
                                    {uses[u] !== undefined ? ` (${uses[u]})` : ""}
                                </option>
                            ))}
                        </select>
                        <select className={select} value={kind} aria-label="Kind" onChange={(e) => setKind(e.target.value)}>
                            <option value="">Any kind</option>
                            <option value="spco">Spec components</option>
                            <option value="spec">Specs</option>
                            <option value="material">Materials</option>
                        </select>
                        <input
                            type="search"
                            value={q}
                            placeholder="search ref, name, parameters"
                            aria-label="Search"
                            className="w-56 rounded-sm border border-gray-700 bg-gray-800 px-2 py-0.5 text-xs text-gray-100 placeholder:text-gray-500"
                            onChange={(e) => setQ(e.target.value)}
                        />
                        <label className="flex items-center gap-1">
                            <input type="checkbox" checked={usage} onChange={(e) => setUsage(e.target.checked)} />
                            count users
                        </label>
                        {current && (
                            <span className="text-gray-500">
                                <SetLabel set={current} /> · {page ? `${page.total} match` : "…"}
                                {page?.usage && !page.usage.indexed ? " · no attribute index to count users in" : ""}
                            </span>
                        )}
                    </>
                )}
            </div>
            {error && <div className="px-3 pb-2 text-xs text-red-400">{error}</div>}
            {items.length > 0 && (
                <div className="max-h-[28rem] overflow-auto">
                    <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-gray-800 text-left text-gray-400">
                            <tr>
                                <th className="px-3 py-1 font-normal">Ref</th>
                                <th className="px-2 py-1 font-normal">For</th>
                                <th className="px-2 py-1 font-normal">Details</th>
                                {usage && <th className="px-2 py-1 font-normal">Used by</th>}
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((item) => (
                                <tr key={`${item.kind}\u0000${item.ref}`} className="border-t border-gray-800 align-top">
                                    <td className="px-3 py-1 font-mono text-gray-100">
                                        <button
                                            type="button"
                                            className="text-left hover:text-sky-300"
                                            title="Copy — what a macro writes after SPRE / MATR"
                                            onClick={() => copy(item.ref)}
                                        >
                                            {item.ref}
                                        </button>
                                        {copied === item.ref && <span className="ml-2 text-emerald-400">copied</span>}
                                        <span className="ml-2 text-gray-500">{item.kind}</span>
                                    </td>
                                    <td className="px-2 py-1 text-gray-300">{item.use ?? "—"}</td>
                                    <td className="px-2 py-1 text-gray-400">{detail(item)}</td>
                                    {usage && <td className="px-2 py-1 text-gray-300">{usedText(item)}</td>}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {page?.next_offset != null && (
                <div className="px-3 py-2">
                    <button
                        type="button"
                        disabled={loading}
                        className="rounded-sm border border-gray-600 px-2 py-0.5 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                        onClick={() => void load(page.next_offset ?? 0)}
                    >
                        {loading ? "Loading…" : `Show more (${page.total - items.length} left)`}
                    </button>
                </div>
            )}
        </AdminSection>
    );
};

export default ReferenceDataSection;
