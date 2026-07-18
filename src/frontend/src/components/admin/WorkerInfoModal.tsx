import React, {useEffect, useState} from "react";
import {ApiError, viewerApi, WorkerEntry, WorkerPackage} from "@/services/viewerApi";

// Per-worker detail modal opened from the info button in WorkersTab. Everything shown here is
// already in hand (the registration entry the row was built from) except the conda package
// manifest, which is lazy-fetched by image tag via the same endpoint the audit log uses.

function fmtDuration(seconds: number): string {
    if (seconds < 0) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
}

function fmtRelative(epoch: number, nowEpoch: number): string {
    const dt = nowEpoch - epoch;
    if (dt < 0) return "in the future";
    if (dt < 60) return `${Math.round(dt)}s ago`;
    if (dt < 3600) return `${Math.round(dt / 60)}m ago`;
    if (dt < 86400) return `${Math.round(dt / 3600)}h ago`;
    return `${Math.round(dt / 86400)}d ago`;
}

function fmtClock(epoch: number): string {
    try {
        return new Date(epoch * 1000).toLocaleString();
    } catch {
        return String(epoch);
    }
}

const Chip: React.FC<{children: React.ReactNode}> = ({children}) => (
    <span className="inline-block bg-gray-700 text-gray-100 text-xs px-2 py-0.5 rounded-sm mr-1 mb-1 font-mono">
        {children}
    </span>
);

const Section: React.FC<{title: string; children: React.ReactNode}> = ({title, children}) => (
    <div className="space-y-1">
        <div className="text-[11px] uppercase tracking-wide text-gray-400">{title}</div>
        {children}
    </div>
);

const Row: React.FC<{label: string; children: React.ReactNode}> = ({label, children}) => (
    <>
        <dt className="text-gray-400">{label}</dt>
        <dd className="text-gray-100 break-all">{children}</dd>
    </>
);

// Conda package manifest for the worker image, lazy-fetched on mount. Mirrors the audit log's
// WorkerPackages disclosure (same endpoint), auto-loaded and always-open in this dedicated modal.
const PackageList: React.FC<{imageTag: string}> = ({imageTag}) => {
    const [pkgs, setPkgs] = useState<WorkerPackage[] | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [filter, setFilter] = useState("");

    useEffect(() => {
        let alive = true;
        viewerApi
            .adminWorkerPackages(imageTag)
            .then((r) => alive && setPkgs(r.packages))
            .catch((e) => alive && setErr(e instanceof ApiError ? e.message : String(e)));
        return () => {
            alive = false;
        };
    }, [imageTag]);

    const f = filter.trim().toLowerCase();
    const shown = (pkgs || []).filter((p) => !f || p.name.toLowerCase().includes(f));

    if (err) return <div className="text-[11px] text-red-400">{err}</div>;
    if (!pkgs) return <div className="text-[11px] text-gray-400">Loading…</div>;
    return (
        <div className="space-y-1">
            <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="filter (ada-cpp, occt…)"
                className="bg-gray-950 border border-gray-700 rounded-sm px-2 py-0.5 text-[11px] text-gray-100 w-48"
            />
            <div className="max-h-56 overflow-auto">
                <dl className="grid grid-cols-[1fr_max-content] gap-x-3 gap-y-0.5 font-mono text-[11px]">
                    {shown.map((p) => (
                        <React.Fragment key={p.name}>
                            <dt className="text-gray-300 break-all">{p.name}</dt>
                            <dd className="text-gray-400 text-right whitespace-nowrap">
                                {p.version ?? "—"}
                                {p.build ? ` (${p.build})` : ""}
                            </dd>
                        </React.Fragment>
                    ))}
                </dl>
            </div>
            <div className="text-[10px] text-gray-500">
                {shown.length} / {pkgs.length} packages
            </div>
        </div>
    );
};

const WorkerInfoModal: React.FC<{worker: WorkerEntry; now: number; onClose: () => void}> = ({
    worker,
    now,
    onClose,
}) => {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    const w = worker;
    const sha = w.image_tag?.replace(/^sha-/, "") || null;
    const conversions = w.conversions ?? [];
    const utilities = w.utilities ?? [];
    const sourceExts = w.source_exts ?? [];

    return (
        <div
            className="fixed inset-0 z-60 flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
            onClick={onClose}
        >
            <div
                className="bg-gray-900 border border-gray-700 rounded-sm shadow-xl flex flex-col max-w-2xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="Worker details"
            >
                <div className="flex items-start gap-3 border-b border-gray-700 px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold flex items-center gap-2">
                            <span
                                className={`inline-block w-2 h-2 rounded-full ${w.online ? "bg-green-400" : "bg-gray-500"}`}
                                title={w.online ? "online" : "offline"}
                            />
                            Worker details
                        </div>
                        <div className="text-xs text-gray-400 font-mono break-all">{w.worker_id}</div>
                    </div>
                    <button
                        type="button"
                        className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2"
                        onClick={onClose}
                        aria-label="Close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>

                <div className="flex-1 overflow-auto p-4 space-y-4 text-sm">
                    <Section title="Identity">
                        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs font-mono">
                            <Row label="Status">{w.online ? "online" : "offline"}</Row>
                            <Row label="Image tag">
                                {w.image_tag || <span className="text-gray-500 italic">—</span>}
                            </Row>
                            <Row label="Commit">{sha || <span className="text-gray-500 italic">—</span>}</Row>
                            <Row label="Started">
                                {fmtClock(w.started_at)} · up {fmtDuration(now - w.started_at)}
                            </Row>
                            <Row label="Heartbeat">
                                {fmtClock(w.last_heartbeat)} · {fmtRelative(w.last_heartbeat, now)}
                            </Row>
                        </dl>
                    </Section>

                    <Section title={`Capabilities (${w.capabilities.length})`}>
                        {w.capabilities.length === 0 ? (
                            <div className="text-xs text-gray-500 italic">none</div>
                        ) : (
                            <div>{w.capabilities.map((c) => <Chip key={c}>{c}</Chip>)}</div>
                        )}
                    </Section>

                    <Section title={`Source extensions (${sourceExts.length})`}>
                        {sourceExts.length === 0 ? (
                            <div className="text-xs text-gray-500 italic">none advertised</div>
                        ) : (
                            <div>{sourceExts.map((e) => <Chip key={e}>{e}</Chip>)}</div>
                        )}
                    </Section>

                    <Section title={`Conversions (${conversions.length})`}>
                        {conversions.length === 0 ? (
                            <div className="text-xs text-gray-500 italic">none advertised</div>
                        ) : (
                            <div className="max-h-40 overflow-auto">
                                <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px]">
                                    {conversions.map((c) => (
                                        <React.Fragment key={c.from}>
                                            <dt className="text-gray-300">{c.from}</dt>
                                            <dd className="text-gray-400 break-all">
                                                → {(c.to ?? []).join(", ") || "—"}
                                            </dd>
                                        </React.Fragment>
                                    ))}
                                </dl>
                            </div>
                        )}
                    </Section>

                    <Section title={`Utilities (${utilities.length})`}>
                        {utilities.length === 0 ? (
                            <div className="text-xs text-gray-500 italic">none advertised</div>
                        ) : (
                            <ul className="text-[11px] space-y-0.5">
                                {utilities.map((u, i) => (
                                    <li key={u.name ?? i} className="break-all">
                                        <span className="font-mono text-gray-200">{u.name ?? `utility ${i}`}</span>
                                        {u.description ? (
                                            <span className="text-gray-400"> — {u.description}</span>
                                        ) : null}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </Section>

                    <Section title="Packages">
                        {w.image_tag ? (
                            <PackageList imageTag={w.image_tag}/>
                        ) : (
                            <div className="text-xs text-gray-500 italic">
                                No image tag — package manifest is keyed by image tag.
                            </div>
                        )}
                    </Section>
                </div>
            </div>
        </div>
    );
};

export default WorkerInfoModal;
