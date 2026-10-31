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

// The properties an admin may set on a material (core's db/catalogue.py MATERIAL_PROPS), SI units.
const MATERIAL_FIELDS: ReadonlyArray<{key: string; label: string; unit: string; text?: boolean}> = [
    {key: "family", label: "Family", unit: "", text: true},
    {key: "grade", label: "Grade", unit: "", text: true},
    {key: "E", label: "E", unit: "Pa"},
    {key: "v", label: "ν", unit: ""},
    {key: "rho", label: "ρ", unit: "kg/m³"},
    {key: "sig_y", label: "σy", unit: "Pa"},
    {key: "sig_u", label: "σu", unit: "Pa"},
    {key: "alpha", label: "α", unit: "1/K"},
];
const FAMILIES = ["carbon_steel", "stainless_steel", "aluminium", "other"];

/** One material's properties: what the provider said (the placeholder), what an admin set (the
 *  value). Saving sends the filled fields as the override; emptying every field removes it. The
 *  override survives a re-export of the reference data. */
const MaterialEditor: React.FC<{set: ReferenceSet; item: ReferenceItem; onSaved: () => void}> = ({set, item, onSaved}) => {
    const values = item.material;
    const [draft, setDraft] = useState<Record<string, string>>(() => {
        const out: Record<string, string> = {};
        for (const f of MATERIAL_FIELDS) {
            const v = values?.override?.[f.key];
            out[f.key] = v === undefined || v === null ? "" : String(v);
        }
        return out;
    });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const save = async () => {
        const key = {provider: set.provider, collection: set.collection, kind: "material", ref: item.ref};
        const props: Record<string, string | number | null> = {};
        for (const f of MATERIAL_FIELDS) {
            const raw = draft[f.key].trim();
            if (raw !== "") props[f.key] = f.text ? raw : Number(raw);
        }
        setBusy(true);
        try {
            if (Object.keys(props).length === 0) await referenceDataApi.deleteOverride(key);
            else await referenceDataApi.putOverride({...key, props});
            setErr(null);
            onSaved();
        } catch (e) {
            setErr(errText(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="flex flex-wrap items-end gap-2 bg-gray-900/60 px-3 py-2 text-xs">
            {MATERIAL_FIELDS.map((f) => {
                const fromProvider = values?.provider?.[f.key];
                const placeholder = fromProvider === undefined || fromProvider === null ? "—" : String(fromProvider);
                return (
                    <label key={f.key} className="flex flex-col gap-0.5 text-gray-400">
                        <span>
                            {f.label}
                            {f.unit && <span className="text-gray-500"> [{f.unit}]</span>}
                        </span>
                        {f.key === "family" ? (
                            <select className={select} value={draft.family} onChange={(e) => setDraft({...draft, family: e.target.value})}>
                                <option value="">{`provider: ${placeholder}`}</option>
                                {FAMILIES.map((x) => (
                                    <option key={x} value={x}>
                                        {x}
                                    </option>
                                ))}
                            </select>
                        ) : (
                            <input
                                type="text"
                                value={draft[f.key]}
                                placeholder={placeholder}
                                title={`Provider: ${placeholder}`}
                                className="w-24 rounded-sm border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-gray-100 placeholder:text-gray-600"
                                onChange={(e) => setDraft({...draft, [f.key]: e.target.value})}
                            />
                        )}
                    </label>
                );
            })}
            <button
                type="button"
                disabled={busy}
                className="rounded-sm border border-sky-700 px-2 py-0.5 text-sky-200 hover:bg-sky-900/40 disabled:opacity-50"
                onClick={() => void save()}
            >
                {busy ? "Saving…" : "Save override"}
            </button>
            <span className="text-gray-500">Placeholder = provider value; filled = override.</span>
            {err && <span className="text-red-400">{err}</span>}
        </div>
    );
};

/** A material's merged properties, `*` marking what an override set. */
function materialText(item: ReferenceItem): string {
    const eff = item.material?.effective;
    if (!eff) return "";
    const src = item.material?.source ?? {};
    return MATERIAL_FIELDS.filter((f) => eff[f.key] !== undefined && eff[f.key] !== null)
        .map((f) => `${f.label}=${String(eff[f.key])}${src[f.key] === "override" ? "*" : ""}`)
        .join(" ");
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
    const [editing, setEditing] = useState<string | null>(null);

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
                                <React.Fragment key={`${item.kind}\u0000${item.ref}`}>
                                <tr className="border-t border-gray-800 align-top">
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
                                    <td className="px-2 py-1 text-gray-400">
                                        {detail(item)}
                                        {item.kind === "material" && (
                                            <>
                                                <span className="ml-2 text-gray-300">{materialText(item)}</span>
                                                <button
                                                    type="button"
                                                    className="ml-2 text-sky-300 hover:underline"
                                                    title="Set the mechanical properties a model built from this material uses (* = set here)"
                                                    onClick={() => setEditing(editing === item.ref ? null : item.ref)}
                                                >
                                                    {editing === item.ref ? "close" : "edit properties"}
                                                </button>
                                            </>
                                        )}
                                    </td>
                                    {usage && <td className="px-2 py-1 text-gray-300">{usedText(item)}</td>}
                                </tr>
                                {editing === item.ref && current && item.kind === "material" && (
                                    <tr>
                                        <td colSpan={usage ? 4 : 3}>
                                            <MaterialEditor
                                                set={current}
                                                item={item}
                                                onSaved={() => {
                                                    setEditing(null);
                                                    void load(0);
                                                }}
                                            />
                                        </td>
                                    </tr>
                                )}
                                </React.Fragment>
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
