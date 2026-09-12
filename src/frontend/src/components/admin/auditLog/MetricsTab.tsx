import React, {useState} from "react";
import {AuditEntry, viewerApi} from "@/services/viewerApi";
import {isMissingManifest, MISSING_MANIFEST_NOTE} from "../workerPackages";
import MetricsHistoryChart from "./MetricsHistoryChart";
import ProfileStatsTable from "./ProfileStatsTable";
import {formatBytes, formatDuration, hasMetrics} from "./format";

// Conversion engine + effective toggles (the convert_meta JSONB). Highlights the
// tessellator that actually ran — an "occ-builtin (fallback …)" value means adacpp
// wasn't present in the worker so libtess2 silently degraded.
const ConvertEngine: React.FC<{meta: import("@/services/viewerApi").ConvertMeta}> = ({meta}) => {
    const fellBack = (meta.tessellator || "").includes("fallback");
    const opts = meta.options || {};
    const optKeys = Object.keys(opts).sort();
    return (
        <div className="pt-2 border-t border-gray-800 space-y-1">
            <div className="text-[11px] uppercase tracking-wide text-gray-400">Conversion engine</div>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 font-mono">
                {meta.tessellator && (
                    <>
                        <dt className="text-gray-400">Tessellator</dt>
                        <dd className={fellBack ? "text-amber-300" : "text-emerald-300"}>{meta.tessellator}</dd>
                    </>
                )}
                {meta.step_glb_pipeline && (
                    <><dt className="text-gray-400">Requested</dt><dd>{meta.step_glb_pipeline}</dd></>
                )}
                {meta.glb_compression && (
                    <><dt className="text-gray-400">Compression</dt><dd>{meta.glb_compression}</dd></>
                )}
                {meta.stream_workers != null && meta.stream_workers !== "" && (
                    <><dt className="text-gray-400">Workers</dt><dd>{String(meta.stream_workers)}</dd></>
                )}
                {meta.convert_ms != null && (
                    <><dt className="text-gray-400">Convert</dt><dd>{meta.convert_ms} ms</dd></>
                )}
                {meta.compress_ms != null && (
                    <>
                        <dt className="text-gray-400">Compress</dt>
                        <dd className="text-sky-300">
                            {meta.compress_ms} ms
                            {meta.convert_ms != null && meta.convert_ms > 0
                                ? ` (+${Math.round((meta.compress_ms / meta.convert_ms) * 100)}%)`
                                : ""}
                        </dd>
                    </>
                )}
            </dl>
            {optKeys.length > 0 && (
                <div className="pt-1">
                    <div className="text-[11px] text-gray-400 mb-0.5">Effective options</div>
                    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 font-mono text-[11px]">
                        {optKeys.map((k) => (
                            <React.Fragment key={k}>
                                <dt className="text-gray-500 break-all">{k}</dt>
                                <dd className="text-gray-300 break-all">{opts[k]}</dd>
                            </React.Fragment>
                        ))}
                    </dl>
                </div>
            )}
        </div>
    );
};

// Worker package manifest ("pixi list") for the row's worker image — lazily
// fetched on expand, filterable. Links a conversion to the exact toolchain
// (occt / pythonocc-core / ada-cpp / …) that produced it.
const WorkerPackages: React.FC<{imageTag: string}> = ({imageTag}) => {
    const [open, setOpen] = useState(false);
    const [pkgs, setPkgs] = useState<import("@/services/viewerApi").WorkerPackage[] | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [missing, setMissing] = useState(false);
    const [filter, setFilter] = useState("");
    const toggle = async () => {
        // `missing` counts as answered: the endpoint will keep saying 404 for
        // this worker, so re-opening must not refetch.
        if (pkgs || err || missing) {
            setOpen((v) => !v);
            return;
        }
        setOpen(true);
        try {
            const r = await viewerApi.adminWorkerPackages(imageTag);
            setPkgs(r.packages);
        } catch (e) {
            // Not every worker records a manifest -- see workerPackages.ts.
            if (isMissingManifest(e)) setMissing(true);
            else setErr(e instanceof Error ? e.message : String(e));
        }
    };
    const f = filter.trim().toLowerCase();
    const shown = (pkgs || []).filter((p) => !f || p.name.toLowerCase().includes(f));
    return (
        <div className="pt-2 border-t border-gray-800 space-y-1">
            <button
                type="button"
                onClick={toggle}
                className="text-[11px] text-blue-300 hover:text-blue-200 flex items-center gap-1.5"
                title="Captured conda package manifest for this worker image"
            >
                <span>{open ? "▾" : "▸"}</span>
                <span>Worker packages</span>
                <span className="font-mono text-gray-500 break-all">{imageTag}</span>
            </button>
            {open && (
                <div className="space-y-1 pl-3">
                    {err && <div className="text-[11px] text-red-400">{err}</div>}
                    {missing && <div className="text-[11px] text-gray-400">{MISSING_MANIFEST_NOTE}</div>}
                    {!err && !missing && !pkgs && <div className="text-[11px] text-gray-400">Loading…</div>}
                    {pkgs && (
                        <>
                            <input
                                value={filter}
                                onChange={(e) => setFilter(e.target.value)}
                                placeholder="filter (occt, ada-cpp…)"
                                className="bg-gray-900 border border-gray-700 rounded-sm px-2 py-0.5 text-[11px] text-gray-100 w-48"
                            />
                            <div className="max-h-64 overflow-auto">
                                <dl className="grid grid-cols-[1fr_max-content] gap-x-3 gap-y-0.5 font-mono text-[11px]">
                                    {shown.map((p) => (
                                        <React.Fragment key={p.name}>
                                            <dt className="text-gray-300 break-all">{p.name}</dt>
                                            <dd className="text-gray-400 text-right whitespace-nowrap">
                                                {p.version}{p.build ? ` (${p.build})` : ""}
                                            </dd>
                                        </React.Fragment>
                                    ))}
                                </dl>
                            </div>
                            <div className="text-[10px] text-gray-500">
                                {shown.length} / {pkgs.length} packages
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
};

// adacpp [STEPPROF-JSON] pipeline summaries (captured when "Profile conversions"
// is on): per-pipeline phase wall/RSS breakdown, kernel-exact peak (VmHWM),
// per-solid stats, achieved parallelism / IO pressure and per-thread utilisation
// — the C++ sibling of the Python profile below.
const CppProfilePanel: React.FC<{profiles: import("@/services/viewerApi").CppProfile[]}> = ({profiles}) => (
    <div className="pt-2 border-t border-gray-800 space-y-3">
        <div className="text-[11px] uppercase tracking-wide text-gray-400">C++ pipeline profile</div>
        {profiles.map((p, i) => {
            const wall = p.wall_ms > 0 ? p.wall_ms : 1;
            return (
                <div key={`${p.label}-${i}`} className="space-y-1">
                    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 font-mono text-[11px]">
                        <dt className="text-gray-400">Pipeline</dt>
                        <dd className="text-emerald-300">{p.label}</dd>
                        <dt className="text-gray-400">Wall</dt>
                        <dd>{formatDuration(p.wall_ms)}</dd>
                        <dt className="text-gray-400">Peak RSS</dt>
                        <dd>{formatBytes(p.peak_rss_mb * 1024 * 1024)} <span className="text-gray-500">(VmHWM)</span></dd>
                        {p.cpu_s != null && (
                            <><dt className="text-gray-400">CPU</dt>
                            <dd>{p.cpu_s.toFixed(1)} s{p.parallelism != null ? ` — ${p.parallelism.toFixed(2)}x cores busy` : ""}</dd></>
                        )}
                        {(p.solids ?? 0) > 0 && (
                            <><dt className="text-gray-400">Solids</dt>
                            <dd>{p.solids}{(p.tris ?? 0) > 0 ? ` (${p.tris} tris, max ${p.max_tris_solid}/solid)` : ""}</dd></>
                        )}
                        {p.disk_read_mb != null && p.disk_read_mb > 0 && (
                            <><dt className="text-gray-400">Disk read</dt>
                            <dd>{p.disk_read_mb.toFixed(0)} MB physical{p.majflt != null ? `, ${p.majflt} major faults` : ""}</dd></>
                        )}
                    </dl>
                    {p.phases.length > 0 && (
                        <table className="w-full text-[11px] font-mono">
                            <thead>
                                <tr className="text-gray-500 text-left">
                                    <th className="font-normal pr-2">phase</th>
                                    <th className="font-normal pr-2 text-right">ms</th>
                                    <th className="font-normal pr-2 text-right">RSS</th>
                                    <th className="font-normal w-1/3">share</th>
                                </tr>
                            </thead>
                            <tbody>
                                {p.phases.map((ph) => (
                                    <tr key={ph.name} className="text-gray-300">
                                        <td className="pr-2 break-all">{ph.name}</td>
                                        <td className="pr-2 text-right">{Math.round(ph.ms)}</td>
                                        <td className="pr-2 text-right">{Math.round(ph.rss_mb)} MB</td>
                                        <td>
                                            <div className="bg-gray-800 rounded-sm h-2 w-full">
                                                <div
                                                    className="bg-sky-600 rounded-sm h-2"
                                                    style={{width: `${Math.min(100, (ph.ms / wall) * 100).toFixed(1)}%`}}
                                                />
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                    {p.notes && Object.keys(p.notes).length > 0 && (
                        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 font-mono text-[11px]">
                            {Object.entries(p.notes).map(([k, v]) => (
                                <React.Fragment key={k}>
                                    <dt className="text-gray-500 break-all">{k}</dt>
                                    <dd className="text-gray-300">{v}</dd>
                                </React.Fragment>
                            ))}
                        </dl>
                    )}
                    {(p.threads?.length ?? 0) > 0 && (
                        <div className="text-[11px] font-mono text-gray-400">
                            threads:{" "}
                            {p.threads!.map((t) => `t${t.tid}=${Math.round(t.busy_ms)}ms/${t.solids}s`).join("  ")}
                        </div>
                    )}
                </div>
            );
        })}
    </div>
);

const MetricsTab: React.FC<{
    entry: AuditEntry;
    onDownloadProfile: () => void;
    downloading: boolean;
    downloadErr: string | null;
}> = ({entry, onDownloadProfile, downloading, downloadErr}) => {
    if (!hasMetrics(entry)) {
        return (
            <div className="px-4 py-3 text-xs text-gray-400">
                No metrics captured for this entry.
                {entry.action !== "convert" ? (
                    <span> Only conversion runs collect resource metrics; a compile run records
                        its wall time and its engine log.</span>
                ) : (
                    <span> The worker that processed this job pre-dates the metrics column.</span>
                )}
            </div>
        );
    }
    return (
        <div className="px-4 py-3 text-xs text-gray-200 space-y-3">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 font-mono">
                <MetricRow label="Wall" value={formatDuration(entry.duration_ms)}/>
                <MetricRow label="CPU user" value={formatDuration(entry.cpu_user_ms)}/>
                <MetricRow label="CPU sys"  value={formatDuration(entry.cpu_sys_ms)}/>
                <MetricRow label="Peak RSS" value={formatBytes((entry.peak_rss_kb ?? null) != null ? (entry.peak_rss_kb as number) * 1024 : null)}/>
                <MetricRow label="Read"     value={formatBytes(entry.read_bytes)}/>
                <MetricRow label="Write"    value={formatBytes(entry.write_bytes)}/>
            </dl>
            {entry.convert_meta && <ConvertEngine meta={entry.convert_meta}/>}
            {(entry.convert_meta?.cpp_profile?.length ?? 0) > 0 && (
                <CppProfilePanel profiles={entry.convert_meta!.cpp_profile!}/>
            )}
            {entry.worker_image_tag && <WorkerPackages imageTag={entry.worker_image_tag}/>}
            <MetricsHistoryChart
                auditId={entry.id}
                cores={entry.convert_meta?.cpu_cores ?? undefined}
                native={entry.convert_meta?.tessellator === "adacpp:native"}
            />
            {entry.profile_key ? (
                <div className="pt-2 border-t border-gray-800 space-y-3">
                    <div>
                        <button
                            type="button"
                            className="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded-sm text-xs disabled:opacity-50"
                            onClick={onDownloadProfile}
                            disabled={downloading}
                        >
                            {downloading ? "Downloading…" : "Download profile (.prof)"}
                        </button>
                        <span className="text-[10px] text-gray-500 ml-2">
                            Loadable in snakeviz / speedscope / pstats.
                        </span>
                        {downloadErr && (
                            <div className="text-red-300 text-[10px] mt-1 break-all">{downloadErr}</div>
                        )}
                    </div>
                    {/* Inline per-function stats — sortable and searchable so
                        the operator can find hot frames without leaving the
                        UI. Server-side pstats parse keeps the SPA bundle
                        free of marshal/pickle parsers. */}
                    <ProfileStatsTable auditId={entry.id} totalWallMs={entry.duration_ms}/>
                </div>
            ) : (
                <div className="text-[10px] text-gray-500 pt-2 border-t border-gray-800">
                    No profile attached. Toggle "Profile conversions" above and re-run.
                </div>
            )}
        </div>
    );
};

const MetricRow: React.FC<{label: string; value: string}> = ({label, value}) => (
    <>
        <dt className="text-gray-400">{label}</dt>
        <dd>{value}</dd>
    </>
);

export default MetricsTab;
