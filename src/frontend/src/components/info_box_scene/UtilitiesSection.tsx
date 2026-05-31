import React from "react";

import {runtime} from "@/runtime/config";
import {viewerApi} from "@/services/viewerApi";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {
    useSceneInfoStore,
    type UtilityKwarg,
    type UtilitySpec,
} from "@/state/sceneInfoStore";
import {applyViewerOps, clearViewerOps} from "@/utils/scene/apply_viewer_ops";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function defaultsFor(spec: UtilitySpec): Record<string, string | number | boolean | null> {
    const out: Record<string, string | number | boolean | null> = {};
    for (const k of spec.kwargs) out[k.name] = (k.default ?? (k.type === "bool" ? false : "")) as never;
    return out;
}

interface VersionBuild {
    commit: string;
    branch: string;
    label: string;                       // "<branch> @ <shortsha>"
    artefacts: {name: string; key: string}[];  // GLB files published under the commit
}

// Parse CI-published build keys (versions/<branch>/<commit>/<artefact>.glb) into
// per-commit builds (each carrying its GLB artefacts). The branch the model is
// loaded from is listed first; the currently-loaded commit is excluded. Also
// returns the loaded model's artefact basename so the file picker can default
// to the matching GLB (like-for-like comparison).
function parseVersionBuilds(
    keys: string[],
    loadedSourceName: string | null,
): {builds: VersionBuild[]; loadedArtefact: string | null} {
    let curBranch: string | null = null;
    let curCommit: string | null = null;
    let loadedArtefact: string | null = null;
    if (loadedSourceName) {
        const p = loadedSourceName.split("/");
        if (p[0] === "versions" && p.length >= 4) {
            curBranch = p[1].replace(/__/g, "/");
            curCommit = p[2];
            loadedArtefact = p[p.length - 1];
        }
    }
    const byCommit = new Map<string, VersionBuild>();
    for (const key of keys) {
        const p = key.split("/");
        if (p[0] !== "versions" || p.length < 4 || !key.endsWith(".glb")) continue;
        const branch = p[1].replace(/__/g, "/");
        const commit = p[2];
        if (commit === curCommit) continue;  // exclude the loaded commit
        const artefact = p[p.length - 1];
        let b = byCommit.get(commit);
        if (!b) {
            b = {commit, branch, label: `${branch} @ ${commit.slice(0, 8)}`, artefacts: []};
            byCommit.set(commit, b);
        }
        b.artefacts.push({name: artefact, key});
    }
    const builds = Array.from(byCommit.values());
    for (const b of builds) b.artefacts.sort((a, c) => a.name.localeCompare(c.name));
    builds.sort((a, b) => {
        const ac = a.branch === curBranch ? 0 : 1;
        const bc = b.branch === curBranch ? 0 : 1;
        return ac - bc || a.label.localeCompare(b.label);
    });
    return {builds, loadedArtefact};
}

// Chained commit + GLB-file picker for a 'ref' kwarg. The kwarg value is the
// full blob key of the selected artefact (resolve_ref_glb uses it verbatim).
function RefField({
    builds,
    loadedArtefact,
    value,
    onChange,
}: {
    builds: VersionBuild[];
    loadedArtefact: string | null;
    value: string | number | boolean | null;
    onChange: (v: string) => void;
}) {
    const common = "text-sm rounded-sm px-1 py-0.5 bg-white text-black w-full";
    const ownerOf = (key: string) => builds.find((b) => b.artefacts.some((a) => a.key === key));
    const [commit, setCommit] = React.useState<string>(
        () => ownerOf(String(value ?? ""))?.commit ?? builds[0]?.commit ?? "",
    );
    const build = builds.find((b) => b.commit === commit) || builds[0];

    // When the commit (or build list) changes, default the file to the loaded
    // model's artefact name if present, else the first one.
    React.useEffect(() => {
        if (!build) return;
        const preferred = build.artefacts.find((a) => a.name === loadedArtefact) || build.artefacts[0];
        if (preferred && preferred.key !== value) onChange(preferred.key);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [commit, builds.length]);

    if (!builds.length) {
        return <div className="text-xs italic">(no published builds)</div>;
    }
    return (
        <div className="space-y-1">
            <select className={common} value={commit} onChange={(e) => setCommit(e.target.value)}>
                {builds.map((b) => (
                    <option key={b.commit} value={b.commit}>{b.label}</option>
                ))}
            </select>
            <select className={common} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
                {(build?.artefacts || []).map((a) => (
                    <option key={a.key} value={a.key}>{a.name}</option>
                ))}
            </select>
        </div>
    );
}

function KwargField({
    kwarg,
    value,
    onChange,
    builds,
    loadedArtefact,
}: {
    kwarg: UtilityKwarg;
    value: string | number | boolean | null;
    onChange: (v: string | number | boolean | null) => void;
    builds: VersionBuild[];
    loadedArtefact: string | null;
}) {
    const common = "text-sm rounded-sm px-1 py-0.5 bg-white text-black w-full";
    let input: React.ReactNode;
    if (kwarg.type === "ref") {
        input = <RefField builds={builds} loadedArtefact={loadedArtefact} value={value} onChange={onChange}/>;
    } else if (kwarg.type === "enum") {
        input = (
            <select className={common} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
                {(kwarg.enum || []).map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                ))}
            </select>
        );
    } else if (kwarg.type === "bool") {
        input = (
            <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)}/>
        );
    } else if (kwarg.type === "int" || kwarg.type === "float") {
        input = (
            <input
                type="number"
                step={kwarg.type === "int" ? 1 : "any"}
                className={common}
                value={value === null || value === undefined ? "" : String(value)}
                onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
            />
        );
    } else {
        input = (
            <input
                type="text"
                className={common}
                value={String(value ?? "")}
                onChange={(e) => onChange(e.target.value)}
            />
        );
    }
    return (
        <label className="block mb-1.5" title={kwarg.description}>
            <span className="text-xs font-medium">{kwarg.name}</span>
            {input}
        </label>
    );
}

const UtilitiesSection = () => {
    const utilities = useSceneInfoStore((s) => s.utilities);
    const setUtilities = useSceneInfoStore((s) => s.setUtilities);
    const selectedUtility = useSceneInfoStore((s) => s.selectedUtility);
    const setSelectedUtility = useSceneInfoStore((s) => s.setSelectedUtility);
    const kwargs = useSceneInfoStore((s) => s.utilityKwargs);
    const setKwargs = useSceneInfoStore((s) => s.setUtilityKwargs);
    const running = useSceneInfoStore((s) => s.running);
    const setRunning = useSceneInfoStore((s) => s.setRunning);
    const lastResult = useSceneInfoStore((s) => s.lastResult);
    const setLastResult = useSceneInfoStore((s) => s.setLastResult);
    const [refBuilds, setRefBuilds] = React.useState<VersionBuild[]>([]);
    const [loadedArtefact, setLoadedArtefact] = React.useState<string | null>(null);

    // Fetch advertised utilities from /api/config once.
    React.useEffect(() => {
        if (utilities.length) return;
        (async () => {
            try {
                const r = await fetch(`${runtime.apiBase()}/config`);
                const cfg = await r.json();
                const list: UtilitySpec[] = cfg.utilities || [];
                setUtilities(list);
                if (list.length && !selectedUtility) {
                    setSelectedUtility(list[0].name);
                    setKwargs(defaultsFor(list[0]));
                }
            } catch (e) {
                setLastResult({summary: {error: `failed to load utilities: ${String(e)}`}});
            }
        })();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const spec = utilities.find((u) => u.name === selectedUtility) || null;
    const hasRefKwarg = !!spec?.kwargs.some((k) => k.type === "ref");

    // When a utility has a ref kwarg, list the CI-published builds (versions/*)
    // as a branch/commit picker, defaulting the ref kwarg to the first option.
    React.useEffect(() => {
        if (!hasRefKwarg) return;
        (async () => {
            try {
                const scope = scopeUrlPart(useScopeStore.getState().current);
                const files = await viewerApi.listFiles(scope);
                const {builds, loadedArtefact: la} = parseVersionBuilds(
                    files.map((f) => f.key), useModelState.getState().loadedSourceName,
                );
                setRefBuilds(builds);
                setLoadedArtefact(la);
                const refK = spec!.kwargs.find((k) => k.type === "ref");
                if (refK && builds.length && !kwargs[refK.name]) {
                    const first = builds[0];
                    const pref = first.artefacts.find((a) => a.name === la) || first.artefacts[0];
                    if (pref) setKwargs({...kwargs, [refK.name]: pref.key});
                }
            } catch {
                setRefBuilds([]);
            }
        })();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [hasRefKwarg, selectedUtility]);

    const onSelectUtility = (name: string) => {
        setSelectedUtility(name);
        const s = utilities.find((u) => u.name === name);
        if (s) setKwargs(defaultsFor(s));
    };

    const run = async () => {
        if (!spec) return;
        const scope = scopeUrlPart(useScopeStore.getState().current);
        const sourceKey = useModelState.getState().loadedSourceName;
        if (!sourceKey) {
            setLastResult({summary: {error: "No model loaded in the scene."}});
            return;
        }
        setRunning(true);
        setLastResult(null);
        try {
            const job = await viewerApi.runUtility(scope, sourceKey, spec.name, kwargs);
            let status = job;
            for (let i = 0; i < 600 && status.status !== "done" && status.status !== "error"; i++) {
                await sleep(1000);
                status = await viewerApi.convertStatus(job.job_id);
            }
            if (status.status === "error") throw new Error(status.error || "utility failed");
            if (status.status !== "done") throw new Error("utility timed out");
            const buf = await viewerApi.getBlob(scope, status.derived_key);
            const payload = JSON.parse(new TextDecoder().decode(new Uint8Array(buf)));
            await applyViewerOps(payload, scope);
            setLastResult({legend: payload.legend, summary: payload.summary});
        } catch (e) {
            setLastResult({summary: {error: String(e)}});
        } finally {
            setRunning(false);
        }
    };

    if (!utilities.length) {
        return <div className="text-sm italic p-1">No utilities advertised by workers.</div>;
    }

    return (
        <div className="p-1">
            <label className="block mb-2">
                <span className="text-xs font-medium">Utility</span>
                <select
                    className="text-sm rounded-sm px-1 py-0.5 bg-white text-black w-full"
                    value={selectedUtility ?? ""}
                    onChange={(e) => onSelectUtility(e.target.value)}
                >
                    {utilities.map((u) => (
                        <option key={u.name} value={u.name}>{u.name}</option>
                    ))}
                </select>
            </label>
            {spec && <p className="text-xs mb-2 opacity-80">{spec.description}</p>}
            {spec?.kwargs.map((k) => (
                <KwargField
                    key={k.name}
                    kwarg={k}
                    value={kwargs[k.name] ?? null}
                    builds={refBuilds}
                    loadedArtefact={loadedArtefact}
                    onChange={(v) => setKwargs({...kwargs, [k.name]: v})}
                />
            ))}
            <div className="flex gap-2 mt-2">
                <button
                    className="text-sm px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50"
                    disabled={running || !spec}
                    onClick={run}
                >
                    {running ? "Running…" : "Run"}
                </button>
                <button
                    className="text-sm px-2 py-1 rounded-sm bg-gray-600 text-white"
                    onClick={() => {
                        clearViewerOps();
                        setLastResult(null);
                    }}
                >
                    Reset scene
                </button>
            </div>
            {lastResult?.legend && (
                <div className="mt-2">
                    {lastResult.legend.map((l) => (
                        <div key={l.label} className="flex items-center text-xs">
                            <span className="inline-block w-3 h-3 mr-1 rounded-sm" style={{backgroundColor: l.color}}/>
                            <span>{l.label}{l.count !== undefined ? ` (${l.count})` : ""}</span>
                        </div>
                    ))}
                </div>
            )}
            {lastResult?.summary && (
                <pre className="mt-2 text-xs whitespace-pre-wrap bg-black bg-opacity-70 text-gray-100 rounded-sm p-1 max-h-40 overflow-auto">
                    {JSON.stringify(lastResult.summary, null, 1)}
                </pre>
            )}
        </div>
    );
};

export default UtilitiesSection;
