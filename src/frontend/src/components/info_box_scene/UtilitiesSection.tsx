import React from "react";

import CollapsibleSection from "@/components/common/CollapsibleSection";
import FilePickerModal from "@/components/common/FilePickerModal";
import {runtime} from "@/runtime/config";
import {viewerApi, type ScopeUrl} from "@/services/viewerApi";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {
    useSceneInfoStore,
    type UtilityKwarg,
    type UtilitySpec,
} from "@/state/sceneInfoStore";
import {applyViewerOps, clearViewerOps} from "@/utils/scene/apply_viewer_ops";
import {flipToCompared, unflip, isFlipped} from "@/utils/scene/flip_geometry";
import {wasmUtilityFor} from "@/utils/wasm/wasmUtilityRegistry";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function defaultsFor(spec: UtilitySpec): Record<string, string | number | boolean | null> {
    const out: Record<string, string | number | boolean | null> = {};
    for (const k of spec.kwargs) out[k.name] = (k.default ?? (k.type === "bool" ? false : "")) as never;
    return out;
}

// File picker for a 'ref' kwarg — pick the compare file straight from the scope's S3 storage using
// the shared storage-browser tree (FilePickerModal), no string typing. The kwarg value is the chosen
// file's blob key (resolve_ref_glb / the wasm path use it verbatim). Restricted to .glb (the directly
// comparable artefacts); upload non-GLB sources via the normal storage browser if needed.
function RefField({
    value,
    onChange,
    scope,
}: {
    value: string | number | boolean | null;
    onChange: (v: string) => void;
    scope: ScopeUrl;
}) {
    const [open, setOpen] = React.useState(false);
    const cur = String(value ?? "");
    return (
        <div className="flex items-center gap-2">
            <span
                className="text-xs font-mono truncate flex-1 px-1 py-0.5 bg-gray-700 border border-gray-600 rounded-sm"
                title={cur || undefined}
            >
                {cur || "— no compare file —"}
            </span>
            <button
                type="button"
                className="text-xs px-2 py-0.5 rounded-sm bg-gray-600 text-white whitespace-nowrap"
                onClick={() => setOpen(true)}
            >
                Choose…
            </button>
            <FilePickerModal
                open={open}
                scope={scope}
                title="Pick a compare file from scope"
                initialKey={cur || undefined}
                filter={(f) => f.key.toLowerCase().endsWith(".glb")}
                onCancel={() => setOpen(false)}
                onPick={(k) => {
                    onChange(k);
                    setOpen(false);
                }}
            />
        </div>
    );
}

function KwargField({
    kwarg,
    value,
    onChange,
    scope,
}: {
    kwarg: UtilityKwarg;
    value: string | number | boolean | null;
    onChange: (v: string | number | boolean | null) => void;
    scope: ScopeUrl;
}) {
    const common = "text-sm rounded-sm px-1 py-0.5 bg-gray-700 text-gray-100 border border-gray-600 w-full";
    let input: React.ReactNode;
    if (kwarg.type === "ref") {
        input = <RefField value={value} onChange={onChange} scope={scope}/>;
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
    const scope = scopeUrlPart(useScopeStore((s) => s.current)); // reactive: drives the ref file picker
    // "Flip to compared geometry" — load the ref build's model in place of the
    // current one for detailed inspection. Module-level flip state mirrored here
    // so the toggle reflects it; `flipBusy` guards the async load.
    const [flipped, setFlipped] = React.useState<boolean>(isFlipped());
    const [flipBusy, setFlipBusy] = React.useState<boolean>(false);
    // Prefer the in-browser (wasm) implementation when one exists for the selected utility — no NATS
    // job, no server memory. Falls back to the server path on failure (or when the toggle is off).
    const [runInBrowser, setRunInBrowser] = React.useState<boolean>(true);

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

    // The ref kwarg's value is the compared build's GLB key (what we flip to).
    const refKwargName = spec?.kwargs.find((k) => k.type === "ref")?.name;
    const compareKey = refKwargName ? String(kwargs[refKwargName] ?? "") : "";

    const onSelectUtility = (name: string) => {
        unflip();              // leaving the utility shouldn't keep the scene flipped
        setFlipped(false);
        setSelectedUtility(name);
        const s = utilities.find((u) => u.name === name);
        if (s) setKwargs(defaultsFor(s));
    };

    const toggleFlip = async () => {
        if (flipBusy) return;
        if (isFlipped()) {
            unflip();
            setFlipped(false);
            return;
        }
        if (!compareKey) return;
        setFlipBusy(true);
        try {
            await flipToCompared(compareKey);
            setFlipped(true);
        } catch (e) {
            setLastResult({summary: {error: `flip failed: ${String(e)}`}});
            setFlipped(false);
        } finally {
            setFlipBusy(false);
        }
    };

    // NOTE: deliberately no unflip-on-unmount — the flip must survive switching
    // the scene panel to "Section" so the user can cut the compared geometry.
    // Re-mounting initialises `flipped` from isFlipped() so the toggle stays
    // accurate. Unflip happens only on explicit toggle-off / Reset / utility switch.

    const run = async () => {
        if (!spec) return;
        const sourceKey = useModelState.getState().loadedSourceName;
        if (!sourceKey) {
            setLastResult({summary: {error: "No model loaded in the scene."}});
            return;
        }
        setRunning(true);
        setLastResult(null);
        try {
            // In-browser (wasm) fast path: compute the viewer-ops client-side, no server job.
            const wasmUtil = runInBrowser ? wasmUtilityFor(spec.name) : undefined;
            const wasmCtx = {scope, sourceKey, refKey: compareKey, kwargs};
            if (wasmUtil && wasmUtil.canRun(wasmCtx)) {
                try {
                    const payload = await wasmUtil.run(wasmCtx);
                    await applyViewerOps(payload, scope);
                    setLastResult({legend: payload.legend, summary: payload.summary});
                    return;
                } catch (e) {
                    // Surface, but still fall back to the proven server path so the user gets a result.
                    console.warn("in-browser wasm utility failed; falling back to server:", e);
                }
            }
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
                    className="text-sm rounded-sm px-1 py-0.5 bg-gray-700 text-gray-100 border border-gray-600 w-full"
                    value={selectedUtility ?? ""}
                    onChange={(e) => onSelectUtility(e.target.value)}
                >
                    {utilities.map((u) => (
                        <option key={u.name} value={u.name}>{u.name}</option>
                    ))}
                </select>
            </label>
            {spec && <p className="text-xs mb-2 opacity-80">{spec.description}</p>}
            {spec && spec.kwargs.length > 0 && (
                <CollapsibleSection title="Properties" defaultOpen>
                    {spec.kwargs.map((k) => (
                        <KwargField
                            key={k.name}
                            kwarg={k}
                            value={kwargs[k.name] ?? null}
                            scope={scope}
                            onChange={(v) => setKwargs({...kwargs, [k.name]: v})}
                        />
                    ))}
                </CollapsibleSection>
            )}
            {refKwargName && (
                <label
                    className="flex items-center gap-2 mt-1 mb-1 text-xs"
                    title="Hide the current model and load the compared build's geometry in its place for detailed inspection (section planes + selection work on it). Toggle off to return."
                >
                    <input
                        type="checkbox"
                        checked={flipped}
                        disabled={flipBusy || (!flipped && !compareKey)}
                        onChange={toggleFlip}
                    />
                    <span>{flipBusy ? "Flipping…" : "Inspect compared geometry"}</span>
                </label>
            )}
            {wasmUtilityFor(spec?.name) && (
                <label
                    className="flex items-center gap-2 mt-1 mb-1 text-xs"
                    title="Run this utility entirely in your browser (WebAssembly) — no server job, instant. Falls back to the server automatically if it can't (e.g. byCoverage) or on error."
                >
                    <input type="checkbox" checked={runInBrowser} onChange={(e) => setRunInBrowser(e.target.checked)}/>
                    <span>Run in browser (wasm)</span>
                </label>
            )}
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
                        unflip();
                        setFlipped(false);
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
