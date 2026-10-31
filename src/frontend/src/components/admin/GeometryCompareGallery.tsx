import React, {useCallback, useEffect, useState} from "react";

import {
    geometryCompareApi,
    type CompareItem,
    type ComparePage,
    type CompareVerdict,
} from "@/services/api/geometryCompare";

import {GALLERY_VERDICTS, SORTS, VERDICT_TONE, metricLines, nodeOf} from "./geometryCompareView";

// Admin → Providers → a geometry-compare run → Gallery: the elements two providers draw differently,
// side by side -- the first provider's tessellation on the left, the other's on the right, framed by
// the same box so a missing or displaced part shows as such. Filterable by collection, verdict and
// name, sortable by a metric. See `routes/geometry_compare.py`.

const PAGE = 24;

const Picture: React.FC<{pictureKey: string | null; label: string}> = ({pictureKey, label}) => {
    const [url, setUrl] = useState<string | null>(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        if (!pictureKey) return;
        let gone = false;
        let made: string | null = null;
        geometryCompareApi
            .pictureObjectUrl(pictureKey)
            .then((u) => {
                made = u;
                if (gone) URL.revokeObjectURL(u);
                else setUrl(u);
            })
            .catch(() => !gone && setFailed(true));
        return () => {
            gone = true;
            if (made) URL.revokeObjectURL(made);
        };
    }, [pictureKey]);
    return (
        <div className="flex h-32 w-32 items-center justify-center rounded-sm bg-gray-950 text-[10px] text-gray-500">
            {url ? <img src={url} alt={label} title={label} className="h-32 w-32 object-contain" /> : !pictureKey ? "no picture" : failed ? "picture failed" : "…"}
        </div>
    );
};

const Card: React.FC<{item: CompareItem}> = ({item}) => {
    const name = item.name || item.key;
    return (
        <li className="space-y-1 rounded-sm border border-gray-800 p-2" data-testid="geometry-compare-card">
            <div className="flex items-center gap-2">
                <span className={`font-medium ${VERDICT_TONE[item.verdict] ?? ""}`}>{item.verdict}</span>
                <span className="truncate text-gray-200" title={name}>{name}</span>
                <button
                    type="button"
                    className="ml-auto text-[10px] text-gray-400 hover:text-white"
                    title="Copy the element name"
                    onClick={() => void navigator.clipboard?.writeText(name)}
                >
                    copy
                </button>
            </div>
            <div className="flex gap-2">
                <div className="space-y-0.5">
                    <Picture pictureKey={item.picture_a} label={`${item.provider_a}: ${name}`} />
                    <div className="w-32 truncate text-[10px] text-gray-400" title={nodeOf(item, "a") ?? ""}>
                        {item.provider_a}
                        {nodeOf(item, "a") ? ` · ${nodeOf(item, "a")}` : " · —"}
                    </div>
                </div>
                <div className="space-y-0.5">
                    <Picture pictureKey={item.picture_b} label={`${item.provider_b}: ${name}`} />
                    <div className="w-32 truncate text-[10px] text-gray-400" title={nodeOf(item, "b") ?? ""}>
                        {item.provider_b}
                        {nodeOf(item, "b") ? ` · ${nodeOf(item, "b")}` : " · —"}
                    </div>
                </div>
            </div>
            <div className="text-[11px] text-gray-300">{item.reason}</div>
            <div className="text-[10px] text-gray-400">
                {item.collection} · {metricLines(item).join(" · ")}
            </div>
        </li>
    );
};

const GeometryCompareGallery: React.FC<{providerA: string; providerB: string; collections: string[]}> = ({
    providerA,
    providerB,
    collections,
}) => {
    const [verdicts, setVerdicts] = useState<CompareVerdict[]>(["fail", "uncertain"]);
    const [collection, setCollection] = useState<string>("");
    const [q, setQ] = useState("");
    const [sort, setSort] = useState("");
    const [offset, setOffset] = useState(0);
    const [page, setPage] = useState<ComparePage | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setPage(
                await geometryCompareApi.adminGeometryCompareItems({
                    providerA,
                    providerB,
                    collections: collection ? [collection] : collections,
                    verdicts,
                    q,
                    sort,
                    limit: PAGE,
                    offset,
                }),
            );
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    }, [providerA, providerB, collection, collections, verdicts, q, sort, offset]);

    useEffect(() => {
        void load();
    }, [load]);

    const toggle = (v: CompareVerdict) => {
        setOffset(0);
        setVerdicts((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]));
    };
    const counts = page?.counts ?? {};
    return (
        <div className="space-y-2 rounded-sm border border-gray-800 p-2 text-xs" data-testid="geometry-compare-gallery">
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-gray-300">
                    {providerA} vs {providerB}
                </span>
                {[...GALLERY_VERDICTS, "pass" as CompareVerdict].map((v) => (
                    <label key={v} className="flex items-center gap-1">
                        <input type="checkbox" checked={verdicts.includes(v)} onChange={() => toggle(v)} />
                        <span className={VERDICT_TONE[v]}>
                            {v} ({counts[v] ?? 0})
                        </span>
                    </label>
                ))}
                {collections.length > 1 && (
                    <select
                        aria-label="Collection"
                        className="rounded-sm border border-gray-700 bg-gray-800 px-1"
                        value={collection}
                        onChange={(e) => {
                            setOffset(0);
                            setCollection(e.target.value);
                        }}
                    >
                        <option value="">all collections</option>
                        {collections.map((c) => (
                            <option key={c} value={c}>
                                {c}
                            </option>
                        ))}
                    </select>
                )}
                <select
                    aria-label="Sort"
                    className="rounded-sm border border-gray-700 bg-gray-800 px-1"
                    value={sort}
                    onChange={(e) => {
                        setOffset(0);
                        setSort(e.target.value);
                    }}
                >
                    {SORTS.map((s) => (
                        <option key={s.value} value={s.value}>
                            {s.label}
                        </option>
                    ))}
                </select>
                <input
                    type="text"
                    placeholder="element name"
                    aria-label="Element name"
                    className="w-40 rounded-sm border border-gray-700 bg-gray-800 px-1"
                    value={q}
                    onChange={(e) => {
                        setOffset(0);
                        setQ(e.target.value);
                    }}
                />
            </div>
            {error && <div className="text-red-300">{error}</div>}
            {page && page.items.length === 0 && <div className="text-gray-400">Nothing matches.</div>}
            <ul className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(18rem,1fr))]">
                {page?.items.map((it) => <Card key={`${it.collection}/${it.key}`} item={it} />)}
            </ul>
            {page && page.total > PAGE && (
                <div className="flex items-center gap-2 text-gray-400">
                    <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                        ‹ prev
                    </button>
                    <span>
                        {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total}
                    </span>
                    <button type="button" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>
                        next ›
                    </button>
                </div>
            )}
        </div>
    );
};

export default GeometryCompareGallery;
