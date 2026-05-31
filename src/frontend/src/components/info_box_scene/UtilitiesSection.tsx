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

function KwargField({
    kwarg,
    value,
    onChange,
}: {
    kwarg: UtilityKwarg;
    value: string | number | boolean | null;
    onChange: (v: string | number | boolean | null) => void;
}) {
    const common = "text-sm rounded-sm px-1 py-0.5 bg-white text-black w-full";
    let input: React.ReactNode;
    if (kwarg.type === "enum") {
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
                <pre className="mt-2 text-xs whitespace-pre-wrap bg-black bg-opacity-20 rounded-sm p-1 max-h-40 overflow-auto">
                    {JSON.stringify(lastResult.summary, null, 1)}
                </pre>
            )}
        </div>
    );
};

export default UtilitiesSection;
