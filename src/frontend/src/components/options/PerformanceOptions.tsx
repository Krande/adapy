import React from "react";
import {Badge, Section, Select, Slider, Switch} from "@/components/ui";
import {usePerfStore, requestRender} from "@/state/perfStore";
import {useOptionsStore} from "@/state/optionsStore";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {useMeStore} from "@/state/meStore";

// Perf-toggle panel. Each row is an opt-in A/B switch for one rendering-cost lever.
// Defaults reproduce the pre-toggle behaviour so the user starts on a known baseline;
// flipping a row should produce a visible change in the Stats / draw-call panel without a
// reload unless explicitly noted.
//
// Re-chromed onto the design system. Every control drives exactly the setter it drove
// before. The one structural change: the anonymous `<hr>` rules that separated these
// toggles are now named Sections — a divider tells you something changed but not what,
// and with thirteen switches in a column that is the difference between a list you can
// navigate and one you scan.

/** One perf lever. `reloadHint` marks the ones that only apply to the next load. */
const Row: React.FC<{
    checked: boolean;
    onChange: () => void;
    title: string;
    blurb?: string;
    reloadHint?: boolean;
}> = ({checked, onChange, title, blurb, reloadHint}) => (
    <Switch
        checked={checked}
        onChange={onChange}
        label={
            <span className="flex items-center gap-1.5">
                {title}
                {reloadHint && <Badge tone="warn">reload</Badge>}
            </span>
        }
        hint={blurb}
    />
);

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

    const {showPerf, setShowPerf} = useOptionsStore();

    return (
        <div className="flex flex-col gap-4">
            {/* The primary diagnosis lever and the most common reason someone opens this
                section — above the A/B knobs, not among them. */}
            <Switch
                label="Show stats (FPS / draw calls)"
                checked={showPerf}
                onChange={() => setShowPerf(!showPerf)}
            />

            <Section title="Materials">
                <label className="flex flex-col gap-1">
                    <span className="text-xs text-content-muted">Material</span>
                    <Select
                        fieldSize="sm"
                        value={materialMode}
                        onChange={(e) => setMaterialMode(e.target.value as never)}
                    >
                        <option value="standard">MeshStandard (PBR, baseline)</option>
                        <option value="lambert">MeshLambert (cheap fragment, no PBR)</option>
                    </Select>
                    <span className="text-xs text-content-subtle">Takes effect on the next loaded model.</span>
                </label>
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
            </Section>

            <Section title="Rasterisation">
                <Row
                    checked={disableShadowMap}
                    onChange={() => setDisableShadowMap(!disableShadowMap)}
                    title="Disable shadow map"
                    blurb="renderer.shadowMap.enabled = false. No shadow-casting lights today, so this is mostly free anyway."
                />
                <Row
                    checked={!antialias}
                    onChange={() => setAntialias(!antialias)}
                    title="Disable antialias (MSAA)"
                    blurb="WebGLRenderer's MSAA is the heaviest fragment-side knob on iGPUs."
                    reloadHint
                />
                <Slider
                    label={
                        <>
                            Pixel-ratio cap — final DPR is <span className="font-mono">min(devicePixelRatio, cap)</span>. Lower = fewer fragments.
                        </>
                    }
                    min={0.5}
                    max={2.0}
                    step={0.25}
                    value={pixelRatioCap}
                    readout
                    format={(n) => n.toFixed(2)}
                    onValueChange={(n) => {
                        setPixelRatioCap(n);
                        requestRender();
                    }}
                />
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
            </Section>

            <Section title="Loading">
                <Row
                    checked={timeSlicedLoad}
                    onChange={() => setTimeSlicedLoad(!timeSlicedLoad)}
                    title="Time-sliced (non-blocking) load"
                    blurb="Process the model in small per-frame batches during load, yielding to the browser between them. The viewer stays interactive and geometry streams in instead of freezing in one long stall. Total load time is about the same. Takes effect on next model load."
                />
                <Row
                    checked={hideBeamSolids}
                    onChange={() => setHideBeamSolids(!hideBeamSolids)}
                    title="Skip beam-solid load"
                    blurb="Falls back to line elements for beams. The ultimate 'is this what's killing my fps' switch. Takes effect on next FEA load."
                />
                <Row
                    checked={hideElementEdges}
                    onChange={() => setHideElementEdges(!hideElementEdges)}
                    title="Skip element-edge wireframe"
                    blurb="Drops one LineSegments per FEA mesh + saves the AFEG fetch. Takes effect on next FEA load."
                />
            </Section>

            <Section title="Picking">
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
            </Section>

            <AdminMetricsRows />
        </div>
    );
};

// Admin-only instrumentation toggles. Default OFF; when on, the viewer times each model
// load (IO / network / CPU / GPU split) and/or samples the render loop, posting rows to
// the backend that the admin "Frontend Loads" tab aggregates. Hidden entirely for
// non-admins.
const AdminMetricsRows: React.FC = () => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const {
        collectLoadMetrics, setCollectLoadMetrics,
        profileCalls, setProfileCalls,
        collectRenderMetrics, setCollectRenderMetrics,
    } = useViewMetricsStore();

    if (!isAdmin) return null;

    return (
        <Section
            title="Frontend metrics"
            actions={<Badge tone="accent">admin</Badge>}
        >
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
                blurb="Samples the render loop and posts a rolling window: FPS, CPU frame time, draw calls, and true GPU ms (timer query) so CPU-bound vs GPU-bound is clear. Adds per-frame cost — default off."
            />
        </Section>
    );
};

export default PerformanceOptions;
