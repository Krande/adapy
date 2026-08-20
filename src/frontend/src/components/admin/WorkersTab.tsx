import React, {useEffect, useState} from "react";
import {ApiError, viewerApi, WorkerEntry} from "@/services/viewerApi";
import InfoIcon from "@/components/icons/InfoIcon";
import WorkerInfoModal from "./WorkerInfoModal";

// Live view of every worker pod that recently published a heartbeat.
// The endpoint just scans a NATS KV bucket — no DB hit — so the
// 5-second poll is cheap. Rows older than `stale_after_s` (60 s on
// the server) come back with online=false but stay in the list
// briefly so a flapping pod is visible while it restarts.
//
// Two layouts:
// * sm:↑ desktop — table with sticky header.
// * mobile — card-per-row, same fields stacked vertically.

const REFRESH_INTERVAL_MS = 5000;

function fmtRelative(epoch: number, nowEpoch: number): string {
    const dt = nowEpoch - epoch;
    if (dt < 0) return "in the future";
    if (dt < 60) return `${Math.round(dt)}s ago`;
    if (dt < 3600) return `${Math.round(dt / 60)}m ago`;
    if (dt < 86400) return `${Math.round(dt / 3600)}h ago`;
    return `${Math.round(dt / 86400)}d ago`;
}

function fmtDuration(seconds: number): string {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
}

const Dot: React.FC<{online: boolean}> = ({online}) => (
    <span
        className={`inline-block w-2 h-2 rounded-full ${online ? "bg-pass" : "bg-surface-3"}`}
        title={online ? "online" : "offline"}
    />
);

const CapabilityChip: React.FC<{name: string}> = ({name}) => (
    <span className="inline-block bg-surface-2 text-content text-xs px-2 py-0.5 rounded-sm mr-1 mb-1">
        {name}
    </span>
);

const WorkersTab: React.FC = () => {
    const [workers, setWorkers] = useState<WorkerEntry[]>([]);
    const [now, setNow] = useState<number>(Date.now() / 1000);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [pruning, setPruning] = useState(false);
    const [infoWorker, setInfoWorker] = useState<WorkerEntry | null>(null);

    const fetchWorkers = async () => {
        setLoading(true);
        try {
            const r = await viewerApi.adminListWorkers();
            setWorkers(r.workers);
            setNow(r.now);
            setError(null);
        } catch (e) {
            const msg = e instanceof ApiError ? e.message : String(e);
            setError(msg);
        } finally {
            setLoading(false);
        }
    };

    const offlineCount = workers.filter((w) => !w.online).length;

    const pruneWorkers = async () => {
        if (offlineCount === 0) return;
        if (!window.confirm(`Remove ${offlineCount} offline worker registration(s)? Live pods re-register within a heartbeat.`)) {
            return;
        }
        setPruning(true);
        try {
            await viewerApi.adminPruneWorkers();
            await fetchWorkers();
        } catch (e) {
            setError(e instanceof ApiError ? e.message : String(e));
        } finally {
            setPruning(false);
        }
    };

    useEffect(() => {
        fetchWorkers();
        const t = setInterval(fetchWorkers, REFRESH_INTERVAL_MS);
        return () => clearInterval(t);
    }, []);

    return (
        <div className="h-full overflow-auto p-3 sm:p-4 bg-surface-0 text-content">
            <div className="flex items-baseline justify-between mb-3">
                <h2 className="text-sm font-semibold">
                    Workers
                    <span className="ml-2 text-xs text-content-muted">
                        {workers.length} entr{workers.length === 1 ? "y" : "ies"}
                        {loading ? " · refreshing…" : ""}
                    </span>
                </h2>
                <div className="flex gap-2">
                    <button
                        className="text-xs px-2 py-1 bg-surface-2 hover:bg-surface-3 rounded-sm disabled:opacity-40"
                        onClick={pruneWorkers}
                        disabled={pruning || offlineCount === 0}
                        title="Drop offline worker registrations left by crashed / scaled-down pods. Live pods re-register automatically."
                    >
                        {pruning ? "Removing…" : `Remove offline${offlineCount ? ` (${offlineCount})` : ""}`}
                    </button>
                    <button
                        className="text-xs px-2 py-1 bg-surface-2 hover:bg-surface-3 rounded-sm"
                        onClick={fetchWorkers}
                        disabled={loading}
                    >
                        Refresh
                    </button>
                </div>
            </div>

            {error && (
                <div className="mb-3 px-3 py-2 bg-fail-subtle border border-fail rounded-sm text-sm">
                    {error}
                </div>
            )}

            {workers.length === 0 && !loading && !error && (
                <div className="text-sm text-content-muted italic">
                    No workers have registered. Either no worker pods are running,
                    or this deployment isn't using the NATS-backed job queue.
                </div>
            )}

            {/* Desktop table */}
            <table className="hidden sm:table w-full text-sm border-collapse">
                <thead className="bg-surface-0 sticky top-0">
                    <tr className="text-left text-xs uppercase text-content-muted">
                        <th className="px-2 py-2 w-8"></th>
                        <th className="px-2 py-2">Worker id</th>
                        <th className="px-2 py-2">Image tag</th>
                        <th className="px-2 py-2">Capabilities</th>
                        <th className="px-2 py-2">Uptime</th>
                        <th className="px-2 py-2">Last heartbeat</th>
                        <th className="px-2 py-2 w-8"></th>
                    </tr>
                </thead>
                <tbody>
                    {workers.map((w) => (
                        <tr
                            key={w.worker_id}
                            className={`border-b border-edge ${w.online ? "" : "opacity-60"}`}
                        >
                            <td className="px-2 py-1.5"><Dot online={w.online}/></td>
                            <td className="px-2 py-1.5 font-mono text-xs break-all">
                                {w.worker_id}
                            </td>
                            <td className="px-2 py-1.5 font-mono text-xs">
                                {w.image_tag || <span className="text-content-subtle italic">—</span>}
                            </td>
                            <td className="px-2 py-1.5">
                                {w.capabilities.length === 0
                                    ? <span className="text-content-subtle italic">—</span>
                                    : w.capabilities.map((c) => <CapabilityChip key={c} name={c}/>)}
                            </td>
                            <td className="px-2 py-1.5 text-content">
                                {fmtDuration(now - w.started_at)}
                            </td>
                            <td className="px-2 py-1.5 text-content">
                                {fmtRelative(w.last_heartbeat, now)}
                            </td>
                            <td className="px-2 py-1.5 text-right">
                                <button
                                    type="button"
                                    onClick={() => setInfoWorker(w)}
                                    className="text-content-muted hover:text-white p-1 rounded-sm hover:bg-surface-0"
                                    title="Worker details (versions, conversions, packages)"
                                    aria-label="Worker details"
                                >
                                    <InfoIcon className="w-4 h-4"/>
                                </button>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>

            {/* Mobile cards */}
            <div className="sm:hidden space-y-2">
                {workers.map((w) => (
                    <div
                        key={w.worker_id}
                        className={`bg-surface-0 border border-edge rounded-sm p-3 ${w.online ? "" : "opacity-60"}`}
                    >
                        <div className="flex items-center gap-2 mb-1">
                            <Dot online={w.online}/>
                            <span className="font-mono text-xs break-all flex-1">{w.worker_id}</span>
                            <button
                                type="button"
                                onClick={() => setInfoWorker(w)}
                                className="shrink-0 text-content-muted hover:text-white p-1 rounded-sm hover:bg-surface-2"
                                title="Worker details"
                                aria-label="Worker details"
                            >
                                <InfoIcon className="w-5 h-5"/>
                            </button>
                        </div>
                        <div className="text-xs text-content-muted grid grid-cols-2 gap-1">
                            <span>Image</span>
                            <span className="font-mono">{w.image_tag || "—"}</span>
                            <span>Uptime</span>
                            <span>{fmtDuration(now - w.started_at)}</span>
                            <span>Last heartbeat</span>
                            <span>{fmtRelative(w.last_heartbeat, now)}</span>
                        </div>
                        {w.capabilities.length > 0 && (
                            <div className="mt-2">
                                {w.capabilities.map((c) => <CapabilityChip key={c} name={c}/>)}
                            </div>
                        )}
                    </div>
                ))}
            </div>

            {infoWorker && (
                <WorkerInfoModal worker={infoWorker} now={now} onClose={() => setInfoWorker(null)}/>
            )}
        </div>
    );
};

export default WorkersTab;
