import React from "react";
import {usePerfStore, requestRender} from "@/state/perfStore";
import {useOptionsStore} from "@/state/optionsStore";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {useMeStore} from "@/state/meStore";
import {useAdacppRenderStatus} from "@/viewport/adacpp/adacppViewport";

// Phase A perf-toggle panel. Each row is an opt-in A/B switch for one
// rendering-cost lever. Defaults reproduce the pre-toggle behaviour so
// the user starts on a known baseline; flipping a row should produce a
// visible change in the Stats / draw-call panel without a reload
// unless explicitly noted.

const Row: React.FC<{
    checked: boolean;
    onChange: () => void;
    title: string;
    blurb?: string;
    reloadHint?: boolean;
}> = ({checked, onChange, title, blurb, reloadHint}) => (
    <label className="flex items-start space-x-2">
        <input type="checkbox" className="mt-1" checked={checked} onChange={onChange}/>
        <span className="leading-tight">
            {title}
            {reloadHint && (
                <span className="ml-1 text-[10px] uppercase tracking-wide text-amber-300">
                    (reload required)
                </span>
            )}
            {blurb && (
                <span className="block text-xs text-gray-400">{blurb}</span>
            )}
        </span>
    </label>
);

const fmt = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : `${n}`;

/** Renderer backend switch + live state of the experimental adacpp WebGPU renderer. */
const RendererBackendOptions: React.FC = () => {
    const renderBackend = usePerfStore((s) => s.renderBackend);
    const setRenderBackend = usePerfStore((s) => s.setRenderBackend);
    const adacppFrustumCull = usePerfStore((s) => s.adacppFrustumCull);
    const setAdacppFrustumCull = usePerfStore((s) => s.setAdacppFrustumCull);
    const adacppConeCull = usePerfStore((s) => s.adacppConeCull);
    const setAdacppConeCull = usePerfStore((s) => s.setAdacppConeCull);
    const status = useAdacppRenderStatus();
    const st = status.stats;

    return (
        <div className="space-y-1">
            <div className="text-xs text-gray-300">Renderer</div>
            <select
                value={renderBackend}
                onChange={(e) => {
                    setRenderBackend(e.target.value as typeof renderBackend);
                    requestRender();
                }}
                className="bg-gray-700 text-white text-xs rounded-sm px-2 py-1 w-full"
            >
                <option value="three">three.js (WebGL)</option>
                <option value="adacpp-webgpu">adacpp WebGPU (experimental)</option>
            </select>
            {renderBackend === "adacpp-webgpu" && (
                <div className="text-xs text-gray-400 space-y-1">
                    {status.state === "starting" && <div>Starting WebGPU…</div>}
                    {status.state === "failed" && (
                        <div className="text-amber-300">Not available, three.js is drawing: {status.error}</div>
                    )}
                    {status.state === "active" && (
                        <>
                            <div>Adapter: <span className="font-mono">{status.adapter}</span></div>
                            {st && (
                                <div className="font-mono leading-tight">
                                    clusters {fmt(st.clustersDrawn)} / {fmt(st.clustersTotal)}<br/>
                                    triangles {fmt(st.trianglesDrawn)} / {fmt(st.trianglesTotal)}<br/>
                                    draws {st.drawCalls} · cull {st.cullMs.toFixed(2)} ms · sync {(st.syncMs ?? 0).toFixed(2)} ms
                                </div>
                            )}
                            <Row
                                checked={adacppFrustumCull}
                                onChange={() => { setAdacppFrustumCull(!adacppFrustumCull); requestRender(); }}
                                title="Cluster frustum culling"
                            />
                            <Row
                                checked={adacppConeCull}
                                onChange={() => { setAdacppConeCull(!adacppConeCull); requestRender(); }}
                                title="Cluster back-face cone culling"
                                blurb="Single-sided materials only; double-sided shells are never cone-culled."
                            />
                            <div>
                                Not drawn by this renderer yet: section planes, icons, transform gizmos,
                                face highlight, fat-line widths.
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
};

const PerformanceOptions: React.FC = () => {
    const {
        materialMode, setMaterialMode,
        solidsBackfaceCull, setSolidsBackfaceCull,
        solidsSmoothShading, setSolidsSmoothShading,
        disableShadowMap, setDisableShadowMap,
        antialias, setAntialias,
        pixelRatioCap, setPixelRatioCap,
        adaptivePixelRatio, setAdaptivePixelRatio,
        onDemandRender, setOnDemandRender,
        hideBeamSolids, setHideBeamSolids,
        hideElementEdges, setHideElementEdges,
        useFlatPicker, setUseFlatPicker,
        gpuFacePicking, setGpuFacePicking,
        timeSlicedLoad, setTimeSlicedLoad,
    } = usePerfStore();

    // Most toggles re-affect on next mesh load (material / content) or
    // are picked up live by the render loop. Material + content toggles
    // need a fresh mesh; surface that as an inline hint instead of a
    // modal so users can keep iterating.

    const {showPerf, setShowPerf} = useOptionsStore();

    return (
        <div className="space-y-2">
            {/* Top-level Stats toggle — primary diagnosis lever and
                the most common reason someone opens this section.
                Belongs above the A/B knobs. */}
            <label className="flex items-center space-x-2">
                <input
                    type="checkbox"
                    checked={showPerf}
                    onChange={() => setShowPerf(!showPerf)}
                />
                <span>Show Stats (FPS / draw calls)</span>
            </label>

            <hr className="border-gray-600 my-1"/>

            <RendererBackendOptions/>

            <hr className="border-gray-600 my-1"/>

            <div className="font-semibold text-xs uppercase tracking-wide text-gray-400">
                Performance (A/B)
            </div>

            <div className="space-y-1">
                <div className="text-xs text-gray-300">Material</div>
                <select
                    value={materialMode}
                    onChange={(e) => setMaterialMode(e.target.value as any)}
                    className="bg-gray-700 text-white text-xs rounded-sm px-2 py-1 w-full"
                >
                    <option value="standard">MeshStandard (PBR, baseline)</option>
                    <option value="lambert">MeshLambert (cheap fragment, no PBR)</option>
                </select>
                <div className="text-xs text-gray-400">
                    Takes effect on the next loaded model.
                </div>
            </div>

            <Row
                checked={solidsBackfaceCull}
                onChange={() => setSolidsBackfaceCull(!solidsBackfaceCull)}
                title="Backface-cull beam solids"
                blurb="FrontSide instead of DoubleSide for feaBeamSolids — roughly halves rasterised fragments on the solid path."
            />
            <Row
                checked={solidsSmoothShading}
                onChange={() => setSolidsSmoothShading(!solidsSmoothShading)}
                title="Smooth-shade beam solids"
                blurb="Drops flatShading on feaBeamSolids. Smoother look on swept beams, also slightly cheaper fragment work."
            />

            <hr className="border-gray-600 my-1"/>

            <Row
                checked={disableShadowMap}
                onChange={() => setDisableShadowMap(!disableShadowMap)}
                title="Disable shadow-sm map"
                blurb="renderer.shadowMap.enabled = false. No shadow-casting lights today, so this is mostly free anyway."
            />
            <Row
                checked={!antialias}
                onChange={() => setAntialias(!antialias)}
                title="Disable antialias (MSAA)"
                blurb="WebGLRenderer's MSAA is the heaviest fragment-side knob on iGPUs."
                reloadHint
            />

            <div className="space-y-1">
                <div className="text-xs text-gray-300">
                    Pixel ratio cap: <span className="font-mono">{pixelRatioCap.toFixed(2)}</span>
                </div>
                <input
                    type="range"
                    min={0.5}
                    max={2.0}
                    step={0.25}
                    value={pixelRatioCap}
                    onChange={(e) => {
                        setPixelRatioCap(parseFloat(e.target.value));
                        requestRender();
                    }}
                    className="w-full"
                />
                <div className="text-xs text-gray-400">
                    Final DPR = min(devicePixelRatio, cap). Lower = fewer fragments.
                </div>
            </div>

            <Row
                checked={adaptivePixelRatio}
                onChange={() => setAdaptivePixelRatio(!adaptivePixelRatio)}
                title="Adaptive DPR while orbiting"
                blurb="Drops to DPR=1.0 while controls are in motion, restores the cap on release."
            />

            <Row
                checked={onDemandRender}
                onChange={() => setOnDemandRender(!onDemandRender)}
                title="On-demand render"
                blurb="Only renders on controls/animation activity. Big idle win; if a step-change appears stale, nudge the view."
            />

            <Row
                checked={timeSlicedLoad}
                onChange={() => setTimeSlicedLoad(!timeSlicedLoad)}
                title="Time-sliced (non-blocking) load"
                blurb="Process the model in small per-frame batches during load, yielding to the browser between them. The viewer stays interactive and geometry streams in instead of freezing in one long stall. Total load time is about the same. Takes effect on next model load."
            />

            <hr className="border-gray-600 my-1"/>

            <Row
                checked={hideBeamSolids}
                onChange={() => setHideBeamSolids(!hideBeamSolids)}
                title="Skip beam-solid load"
                blurb="Falls back to line elements for beams. The ultimate 'is this what's killing my fps' switch. Takes effect on next FEA load."
            />
            <Row
                checked={useFlatPicker}
                onChange={() => setUseFlatPicker(!useFlatPicker)}
                title="Flat-varying GPU picker"
                blurb="Indexed picker with one provoking vertex per triangle (GLSL3 flat varying). Auto-applies only on meshes with high vertex sharing (α<1.55) — CAD models gain ~30-40%; FEA bakes with per-element vertex sets fall back to non-indexed since flat would cost more there. Takes effect on next model load."
            />
            <Row
                checked={gpuFacePicking}
                onChange={() => setGpuFacePicking(!gpuFacePicking)}
                title="GPU face picking"
                blurb="Resolve the clicked face directly from the GPU picker (per-face pick ids baked from the GLB's face_ranges) instead of a CPU raycast. Instant on merged-by-colour meshes with millions of triangles, where the raycast lags and above ~8M tris returns no face at all. Off = the raycast path. Takes effect on next model load."
            />
            <Row
                checked={hideElementEdges}
                onChange={() => setHideElementEdges(!hideElementEdges)}
                title="Skip element-edge wireframe"
                blurb="Drops one LineSegments per FEA mesh + saves the AFEG fetch. Takes effect on next FEA load."
            />

            <AdminMetricsRows/>
        </div>
    );
};

// Admin-only instrumentation toggles. Default OFF; when on, the viewer
// times each model load (IO / network / CPU / GPU split) and/or samples
// the render loop, posting rows to the backend that the admin "Frontend
// Loads" tab aggregates. Hidden entirely for non-admins.
const AdminMetricsRows: React.FC = () => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const {
        collectLoadMetrics, setCollectLoadMetrics,
        profileCalls, setProfileCalls,
        collectRenderMetrics, setCollectRenderMetrics,
    } = useViewMetricsStore();

    if (!isAdmin) return null;

    return (
        <>
            <hr className="border-gray-600 my-1"/>
            <div className="font-semibold text-xs uppercase tracking-wide text-purple-300">
                Frontend metrics (admin)
            </div>
            <Row
                checked={collectLoadMetrics}
                onChange={() => setCollectLoadMetrics(!collectLoadMetrics)}
                title="Record model-load metrics"
                blurb="Times each load phase (TTFB / download / parse / prepare / first-render) plus payload + device, and posts it to the Frontend Loads admin dashboard. Default off."
            />
            <Row
                checked={profileCalls}
                onChange={() => setProfileCalls(!profileCalls)}
                title="Profile calls during load (TS + WASM)"
                blurb="Runs the JS Self-Profiling API during a load to capture per-function self-time hotspots. Needs the Document-Policy: js-profiling header (Chromium); silently skipped otherwise. Only used when load metrics are on."
            />
            <Row
                checked={collectRenderMetrics}
                onChange={() => setCollectRenderMetrics(!collectRenderMetrics)}
                title="Record render metrics (FPS / draw calls / GPU)"
                blurb="Samples the render loop and posts a rolling window: FPS, CPU frame time, draw calls, and true GPU ms (timer query) so CPU-bound vs GPU-bound is clear, main-thread long tasks, and a connected 3D mouse's report cadence. Adds per-frame cost — default off."
            />
        </>
    );
};

export default PerformanceOptions;
