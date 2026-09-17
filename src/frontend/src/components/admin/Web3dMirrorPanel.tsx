import React, {useCallback, useEffect, useState} from "react";

import {viewerApi} from "@/services/viewerApi";
import {MirrorReport, mirrorStatus, readActionResult, startMirrorSync} from "@/services/externalModels";
import {ConvertStatus, useConversionStore} from "@/state/conversionStore";

// The web3d cache, and the switch that governs it.
//
// WHAT THIS IS FOR. web3d publishes one GLB per E3D site, and reaching them
// through web3d's own access API needs an identity carrying an Aibel Repro
// allocation -- so every consumer so far has had to sign a real person in
// interactively before it could read anything. A read-only service principal on
// the storage account goes around that, and the worker copies what it finds
// into this deployment's own external-model store. From then on the viewer
// serves those GLBs like any other external model and a user needs nothing but
// the session they already have.
//
// WHY THE SWITCH IS A SETTING AND NOT A BUTTON THAT DOES THE WORK. Settings
// live in the API's database and the worker that runs a plugin job has no pool,
// so the process doing the mirroring cannot read this key. Both CALLERS can:
// this panel, and the scheduled job through the API with a CLI token. So the
// toggle is enforced HERE and there, over a deployment-level environment
// variable underneath that an operator with shell access can always reach.
// Writing it admin-only and reading it publicly is why the key is `public.`-
// prefixed -- every user's UI may need to know whether the cache is live, and
// only an admin may change that.

const CATALOGUE_SCOPE = "shared";
const MIRROR_SETTING_KEY = "public.external_models.web3d_mirror";

interface MirrorSetting {
    enabled: boolean;
    projects: string[];
}

function parseSetting(raw: unknown): MirrorSetting {
    let value = raw;
    if (typeof value === "string") {
        try {
            value = JSON.parse(value);
        } catch {
            value = null;
        }
    }
    const obj = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
    const projects = Array.isArray(obj.projects)
        ? obj.projects.map((p) => String(p).trim()).filter(Boolean)
        : [];
    return {enabled: Boolean(obj.enabled), projects};
}

interface Props {
    /** The provider whose store holds the cache. The mirror always goes through
     *  the worker, so this is a worker-side provider id and never a browser-side
     *  one -- the point of the mirror is that no signed-in user is in the path. */
    provider: string;
}

const Web3dMirrorPanel: React.FC<Props> = ({provider}) => {
    const [setting, setSetting] = useState<MirrorSetting>({enabled: false, projects: []});
    const [report, setReport] = useState<MirrorReport | null>(null);
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    // Not an error state: the common reason a status read fails is that this
    // deployment has no web3d credential, which is a FACT about the deployment
    // and not a fault. The backend's refusals are written to be read by a
    // person, so they are shown verbatim rather than translated here.
    const [unavailable, setUnavailable] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [projectsDraft, setProjectsDraft] = useState("");

    const loadSetting = useCallback(async () => {
        try {
            const raw = await viewerApi.getPublicSetting(MIRROR_SETTING_KEY);
            const parsed = parseSetting(raw);
            setSetting(parsed);
            setProjectsDraft(parsed.projects.join(", "));
        } catch {
            // An unset key is the normal first state, not a failure.
            setSetting({enabled: false, projects: []});
        }
    }, []);

    // `checkSource` false skips one HEAD against web3d per site. The first paint
    // wants what is cached and not a round trip per model; entries then report
    // staleness as unknown, which the table renders as "—" rather than as
    // up to date. Pressing Check asks the real question.
    const loadStatus = useCallback(
        async (checkSource: boolean) => {
            setLoading(true);
            setError(null);
            setUnavailable(null);
            try {
                const out = await mirrorStatus(provider, CATALOGUE_SCOPE, {checkSource});
                setReport(out);
            } catch (e) {
                setReport(null);
                setUnavailable(e instanceof Error ? e.message : String(e));
            } finally {
                setLoading(false);
            }
        },
        [provider],
    );

    useEffect(() => {
        void loadSetting();
        void loadStatus(false);
    }, [loadSetting, loadStatus]);

    const persist = useCallback(
        async (next: MirrorSetting) => {
            setBusy("setting");
            setError(null);
            try {
                await viewerApi.adminSetSetting(MIRROR_SETTING_KEY, JSON.stringify(next));
                setSetting(next);
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
            } finally {
                setBusy(null);
            }
        },
        [],
    );

    // A SYNC IS NOT AWAITED. A first mirror of a project is hundreds of sites
    // and tens of minutes -- awaiting it holds this panel open on a promise
    // nobody should have to sit in front of, and closing the panel would then
    // look like cancelling a transfer that is in fact still running.
    //
    // So it enqueues, reports into the SAME global toast the model loader and
    // the conversions use, and polls in the background. The button frees itself
    // as soon as the job is accepted. The worker ticks that job once per SITE
    // transferred, so the bar moves while it works rather than once at the end.
    const sync = useCallback(
        async (force: boolean) => {
            setBusy(force ? "resync" : "sync");
            setError(null);

            const toastKey = "util:web3d-mirror";
            const cs = useConversionStore.getState();
            const push = (s: {job_id: string; status: string; progress?: number; stage?: string; error?: string | null}) =>
                cs.setJob(toastKey, {
                    sourceKey: force ? "web3d cache (full re-sync)" : "web3d cache",
                    jobId: s.job_id,
                    derivedKey: "",
                    status: (s.status as ConvertStatus) || "running",
                    progress: s.progress ?? 0,
                    stage: s.stage || "mirroring",
                    error: s.error ?? null,
                    startedAt: Date.now(),
                });

            let job;
            try {
                job = await startMirrorSync(provider, CATALOGUE_SCOPE, {force});
            } catch (e) {
                // The ENQUEUE failing is this panel's problem to show: it means
                // the deployment refused before any work started, and the toast
                // would otherwise appear and vanish with no explanation.
                setError(e instanceof Error ? e.message : String(e));
                setBusy(null);
                return;
            }
            push({job_id: job.job_id, status: "running", stage: "mirroring"});
            setBusy(null);

            void (async () => {
                try {
                    let status = await viewerApi.convertStatus(job.job_id);
                    // Ten minutes at one-second intervals is not a timeout on
                    // the TRANSFER -- the worker goes on regardless -- it is a
                    // bound on how long this page watches one.
                    for (let i = 0; i < 600 && status.status !== "done" && status.status !== "error"; i++) {
                        await new Promise((r) => setTimeout(r, 1000));
                        status = await viewerApi.convertStatus(job.job_id);
                        push(status);
                    }
                    push(status);
                    if (status.status === "done") {
                        const out = await readActionResult<MirrorReport>(CATALOGUE_SCOPE, job.derived_key);
                        setReport(out);
                    } else if (status.status === "error") {
                        setError(status.error || "the sync failed");
                    }
                } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                }
            })();
        },
        [provider],
    );

    const projects = report ? Object.entries(report.projects) : [];
    const anyStale = projects.some(([, p]) => p.stale > 0);
    const anyMissing = projects.some(([, p]) => p.cached < p.total);

    return (
        <div className="px-3 py-3 border-b border-gray-700 space-y-2">
            <div className="flex items-center gap-2">
                <div className="text-sm font-medium flex-1">web3d cache</div>
                <label className="flex items-center gap-1 text-xs">
                    <input
                        type="checkbox"
                        checked={setting.enabled}
                        disabled={busy !== null}
                        onChange={(e) => void persist({...setting, enabled: e.target.checked})}
                    />
                    Enabled
                </label>
                <button
                    type="button"
                    className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800 disabled:opacity-50"
                    disabled={loading || busy !== null}
                    onClick={() => void loadStatus(true)}
                >
                    {loading ? "Checking…" : "Check"}
                </button>
                <button
                    type="button"
                    className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800 disabled:opacity-50"
                    disabled={!setting.enabled || busy !== null || unavailable !== null}
                    title={
                        setting.enabled
                            ? "Transfer anything web3d has changed since it was last cached"
                            : "Switch the cache on first"
                    }
                    onClick={() => void sync(false)}
                >
                    {busy === "sync" ? "Starting…" : "Sync now"}
                </button>
            </div>

            <div className="text-xs text-gray-400">
                Mirrored GLBs are served from this deployment&rsquo;s own store, so anyone signed in
                can open them. Nobody signs in to web3d. A sync runs in the background &mdash; watch
                it in the progress toast; the counts here refresh when it finishes.
            </div>

            <div className="flex items-center gap-2">
                <label className="text-xs text-gray-400" htmlFor="web3d-projects">
                    Projects
                </label>
                <input
                    id="web3d-projects"
                    className="flex-1 text-xs bg-gray-900 border border-gray-700 rounded-sm px-2 py-1"
                    placeholder="ASP, GUA — comma separated"
                    value={projectsDraft}
                    disabled={busy !== null}
                    onChange={(e) => setProjectsDraft(e.target.value)}
                    onBlur={() => {
                        const next = projectsDraft
                            .split(",")
                            .map((p) => p.trim())
                            .filter(Boolean);
                        if (next.join(",") !== setting.projects.join(",")) {
                            void persist({...setting, projects: next});
                        }
                    }}
                />
            </div>

            {unavailable && (
                <div className="text-xs text-amber-300 border border-amber-900/60 rounded-sm px-2 py-1">
                    {unavailable}
                </div>
            )}
            {error && <div className="text-xs text-red-300">{error}</div>}

            {report && projects.length > 0 && (
                <table className="w-full text-xs">
                    <thead className="text-left text-gray-500 uppercase">
                        <tr>
                            <th className="py-1 font-medium">Project</th>
                            <th className="py-1 font-medium">Sites</th>
                            <th className="py-1 font-medium">Cached</th>
                            <th className="py-1 font-medium">Stale</th>
                            <th className="py-1 font-medium">Transferred</th>
                        </tr>
                    </thead>
                    <tbody>
                        {projects.map(([key, p]) => (
                            <tr key={key} className="border-t border-gray-800">
                                <td className="py-1">{key}</td>
                                <td className="py-1">{p.total}</td>
                                <td className="py-1">
                                    {p.cached}
                                    {p.cached < p.total && (
                                        <span className="text-amber-300"> ({p.total - p.cached} missing)</span>
                                    )}
                                </td>
                                {/* `unknown` is why this is not just a number: a status
                                    read that skipped the upstream HEAD knows nothing
                                    about staleness, and showing 0 there would claim
                                    the cache is current on no evidence. */}
                                <td className="py-1">
                                    {p.unknown === p.total ? (
                                        <span className="text-gray-500" title="not checked against web3d">
                                            &mdash;
                                        </span>
                                    ) : (
                                        <span className={p.stale ? "text-amber-300" : undefined}>{p.stale}</span>
                                    )}
                                </td>
                                <td className="py-1">
                                    {p.transferred.length}
                                    {Object.keys(p.failed).length > 0 && (
                                        <span className="text-red-300">
                                            {" "}
                                            ({Object.keys(p.failed).length} failed)
                                        </span>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}

            {report && (anyStale || anyMissing) && setting.enabled && (
                <div className="text-xs text-amber-300">
                    web3d has rebuilt since this cache was filled. Sync to pick it up.
                </div>
            )}
            {report && !setting.enabled && (
                <div className="text-xs text-gray-500">
                    The cache is switched off, so nothing refreshes it — what is listed above is
                    whatever was last mirrored.
                </div>
            )}
        </div>
    );
};

export default Web3dMirrorPanel;
