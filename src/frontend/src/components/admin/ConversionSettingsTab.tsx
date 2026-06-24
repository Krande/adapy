import React, {useEffect, useMemo, useState} from "react";
import {ApiError, viewerApi} from "@/services/viewerApi";

// Per-deployment conversion knobs. Each row maps to a key in the
// app_settings table; a falsy value (or absence) means "use adapy's
// in-code default", so flipping a row off doesn't accidentally pin
// it to ``false``. The worker reads these fresh per job and applies
// them as ADA_* env vars inside the fork-child only — no restart
// needed, sibling jobs unaffected.

type TriState = "unset" | "on" | "off";

interface SettingRow {
    key: string;
    label: string;
    description: string;
    codeDefault: boolean;
}

const ROWS: SettingRow[] = [
    {
        key: "use_sat_pcurves",
        label: "Use SAT pcurves",
        description:
            "Consume the 2D parameter-space curves authored in the SAT/Genie input " +
            "instead of regenerating them from the 3D edges. Off → safer fallback that " +
            "is ~5x slower and may visually diverge for some BSpline faces.",
        codeDefault: true,
    },
    {
        key: "skip_shapefix",
        label: "Skip ShapeFix",
        description:
            "Skip the post-build ShapeFix_Face pass even when SAT pcurves are off. " +
            "ShapeFix would otherwise rebuild authored pcurves to OCCT conventions, " +
            "undoing per-face consistency. Auto-skipped when ``Use SAT pcurves`` is on.",
        codeDefault: false,
    },
    {
        key: "merge_meshes",
        label: "Merge GLB meshes",
        description:
            "Trimesh merges meshes by material when exporting GLB. Off → one mesh per " +
            "face (face names preserved, file is larger). Useful for visually inspecting " +
            "which face is which in a broken conversion.",
        codeDefault: true,
    },
    {
        key: "step_streamer_auto",
        label: "Auto-stream large STEP",
        description:
            "Convert large STEP→GLB with the memory-bounded streaming reader (one solid " +
            "at a time) instead of OpenCASCADE, so huge assemblies don't OOM-kill the " +
            "worker. The size threshold is set below. Skips solids using unsupported " +
            "(spherical / rational B-spline) surfaces. The per-file “Load using " +
            "streamer” action overrides this per job.",
        codeDefault: true,
    },
    {
        key: "fea_sin_streamer",
        label: "Stream SIN FEA bake",
        description:
            "Bake Sesam .sin FEA results with the memory-bounded per-step streaming reader " +
            "instead of materialising the whole multi-step result. ~1.7x slower, but peak RSS " +
            "stays flat in step count — use it for many-mode / large decks that OOM the worker. " +
            "On this path step labels fall back to the mode index (no SESTRA.LIS eigen-frequency " +
            "enrichment). Off → adapy's default full-materialise reader.",
        codeDefault: false,
    },
    {
        key: "ifc_streaming",
        label: "Stream IFC write",
        description:
            "Write FEM→IFC with the memory-bounded streaming writer: Plate solids are " +
            "hand-authored as SPF text instead of holding the whole model in memory, " +
            "~halving peak RSS so large shell meshes (100k+ plates) don't OOM the worker. " +
            "On by default. Off → the in-memory writer (needed if a model uses groups, " +
            "presentation layers or welds, which the streaming path omits for plates).",
        codeDefault: true,
    },
    {
        key: "profile_conversions",
        label: "Profile conversions",
        description:
            "Run the worker fork-child under cProfile and upload the .prof to the audit " +
            "row. Adds a few % overhead and bloats audit storage; intended for short " +
            "debugging windows, not production.",
        codeDefault: false,
    },
];

const STREAMER_THRESHOLD_KEY = "step_streamer_threshold_mb";
const SOLID_TIMEOUT_KEY = "step_stream_solid_timeout_s";

function parseTri(raw: string | null): TriState {
    const v = (raw || "").trim().toLowerCase();
    if (!v) return "unset";
    if (["1", "true", "yes", "on"].includes(v)) return "on";
    if (["0", "false", "no", "off"].includes(v)) return "off";
    return "unset";
}

function triToString(tri: TriState): string {
    if (tri === "on") return "true";
    if (tri === "off") return "false";
    return "";
}

const TIMEOUT_KEY = "conversion_timeout_minutes";

// STEP→GLB tessellation engine. Empty = adapy's code default (libtess2). Maps to
// the ADAPY_STEP_GLB_PIPELINE env the worker applies per job; per-job convert-dialog
// overrides win over this global default.
const STEP_GLB_PIPELINE_KEY = "step_glb_pipeline";
const PIPELINE_OPTIONS: {value: string; label: string}[] = [
    {value: "", label: "Unset — adapy default (libtess2)"},
    {value: "libtess2", label: "libtess2 — adacpp OCC-free (full curved geometry)"},
    {value: "occ-builtin", label: "occ-builtin — OpenCASCADE (drops some curved surfaces)"},
    {value: "step2glb", label: "step2glb — external binary"},
    {value: "adacpp-occ", label: "adacpp-occ — taxonomy / OCCT kernel"},
    {value: "adacpp-cgal", label: "adacpp-cgal — taxonomy / CGAL kernel"},
    {value: "adacpp-hybrid", label: "adacpp-hybrid — taxonomy / hybrid kernel"},
];

const ConversionSettingsTab: React.FC = () => {
    const [values, setValues] = useState<Record<string, TriState>>(() =>
        Object.fromEntries(ROWS.map((r) => [r.key, "unset"])),
    );
    const [timeoutMinutes, setTimeoutMinutes] = useState("");
    const [timeoutSaving, setTimeoutSaving] = useState(false);
    const [timeoutSavedAt, setTimeoutSavedAt] = useState<number | null>(null);
    const [streamerThreshold, setStreamerThreshold] = useState("");
    const [streamerSaving, setStreamerSaving] = useState(false);
    const [streamerSavedAt, setStreamerSavedAt] = useState<number | null>(null);
    const [solidTimeout, setSolidTimeout] = useState("");
    const [solidTimeoutSaving, setSolidTimeoutSaving] = useState(false);
    const [solidTimeoutSavedAt, setSolidTimeoutSavedAt] = useState<number | null>(null);
    const [pipeline, setPipeline] = useState("");
    const [pipelineSaving, setPipelineSaving] = useState(false);
    const [pipelineSavedAt, setPipelineSavedAt] = useState<number | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState<Record<string, boolean>>({});
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const next: Record<string, TriState> = {};
                for (const row of ROWS) {
                    const v = await viewerApi.adminGetSetting(row.key);
                    next[row.key] = parseTri(v);
                }
                const t = await viewerApi.adminGetSetting(TIMEOUT_KEY);
                if (!cancelled) setTimeoutMinutes((t || "").trim());
                const thr = await viewerApi.adminGetSetting(STREAMER_THRESHOLD_KEY);
                if (!cancelled) setStreamerThreshold((thr || "").trim());
                const sto = await viewerApi.adminGetSetting(SOLID_TIMEOUT_KEY);
                if (!cancelled) setSolidTimeout((sto || "").trim());
                const pp = await viewerApi.adminGetSetting(STEP_GLB_PIPELINE_KEY);
                if (!cancelled) setPipeline((pp || "").trim());
                if (!cancelled) setValues(next);
            } catch (e) {
                if (!cancelled) setError(e instanceof ApiError ? e.detail || e.message : String(e));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const onChange = async (key: string, next: TriState) => {
        setSaving((s) => ({...s, [key]: true}));
        try {
            const stringValue = triToString(next);
            // Empty string clears the override at the server. The
            // generic adminSetSetting endpoint accepts an empty value
            // (and the worker treats it as "no override").
            await viewerApi.adminSetSetting(key, stringValue);
            setValues((v) => ({...v, [key]: next}));
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setSaving((s) => ({...s, [key]: false}));
        }
    };

    const onTimeoutSave = async () => {
        const raw = timeoutMinutes.trim();
        if (raw !== "") {
            const n = Number(raw);
            if (Number.isNaN(n) || n < 0) {
                setError(`timeout must be a non-negative number (got "${raw}")`);
                return;
            }
        }
        setTimeoutSaving(true);
        setError(null);
        try {
            await viewerApi.adminSetSetting(TIMEOUT_KEY, raw);
            setTimeoutSavedAt(Date.now());
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setTimeoutSaving(false);
        }
    };

    const onStreamerThresholdSave = async () => {
        const raw = streamerThreshold.trim();
        if (raw !== "") {
            const n = Number(raw);
            if (Number.isNaN(n) || n < 0) {
                setError(`threshold must be a non-negative number (got "${raw}")`);
                return;
            }
        }
        setStreamerSaving(true);
        setError(null);
        try {
            await viewerApi.adminSetSetting(STREAMER_THRESHOLD_KEY, raw);
            setStreamerSavedAt(Date.now());
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setStreamerSaving(false);
        }
    };

    const onSolidTimeoutSave = async () => {
        const raw = solidTimeout.trim();
        if (raw !== "") {
            const n = Number(raw);
            if (Number.isNaN(n) || n <= 0) {
                setError(`solid timeout must be a positive number of seconds (got "${raw}")`);
                return;
            }
        }
        setSolidTimeoutSaving(true);
        setError(null);
        try {
            await viewerApi.adminSetSetting(SOLID_TIMEOUT_KEY, raw);
            setSolidTimeoutSavedAt(Date.now());
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setSolidTimeoutSaving(false);
        }
    };

    const onPipelineChange = async (next: string) => {
        setPipelineSaving(true);
        setError(null);
        try {
            await viewerApi.adminSetSetting(STEP_GLB_PIPELINE_KEY, next);
            setPipeline(next);
            setPipelineSavedAt(Date.now());
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setPipelineSaving(false);
        }
    };

    const anySaving = useMemo(() => Object.values(saving).some(Boolean), [saving]);

    return (
        <div className="flex flex-col h-full">
            <div className="px-3 sm:px-4 py-3 border-b border-gray-700 text-xs text-gray-300">
                Per-deployment conversion knobs. Empty (Unset) means adapy uses its
                in-code default; toggling On/Off pins the value globally. Per-job
                overrides on the convert dialog win over these.
                {anySaving ? <span className="ml-2 text-gray-400">(saving…)</span> : null}
            </div>
            {error && (
                <div className="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700">
                    {error}
                </div>
            )}
            <div className="flex-1 min-h-0 overflow-auto">
                {!loading && (
                    <div className="px-3 sm:px-4 py-3 border-b border-gray-800 space-y-2">
                        <div>
                            <div className="font-medium text-sm">STEP→GLB tessellator</div>
                            <div className="text-[11px] text-gray-400 font-mono">
                                {STEP_GLB_PIPELINE_KEY}
                            </div>
                        </div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <select
                                value={pipeline}
                                onChange={(e) => onPipelineChange(e.target.value)}
                                disabled={pipelineSaving}
                                className="bg-gray-900 border border-gray-700 rounded-sm px-2 py-1 text-sm text-gray-100 max-w-full"
                            >
                                {PIPELINE_OPTIONS.map((o) => (
                                    <option key={o.value} value={o.value}>{o.label}</option>
                                ))}
                            </select>
                            {pipelineSaving && <span className="text-[11px] text-gray-400">saving…</span>}
                            {pipelineSavedAt && !pipelineSaving && (
                                <span className="text-[11px] text-emerald-400">
                                    saved {Math.floor((Date.now() - pipelineSavedAt) / 1000)}s ago
                                </span>
                            )}
                        </div>
                        <div className="text-xs text-gray-400 max-w-2xl">
                            Engine for STEP→GLB tessellation. <span className="font-mono">libtess2</span>{" "}
                            (the code default when Unset) is adacpp's OCC-free boundary tessellator — it
                            renders the curved surfaces (rational B-spline / spherical / conical / toroidal)
                            the OpenCASCADE streaming reader silently drops.{" "}
                            <span className="font-mono">occ-builtin</span> is the prior OpenCASCADE path;
                            <span className="font-mono"> adacpp-occ/cgal/hybrid</span> use adacpp's taxonomy
                            kernels; <span className="font-mono">step2glb</span> runs the external binary.
                            Per-job overrides on the convert dialog win over this.
                        </div>
                    </div>
                )}
                {!loading && (
                    <div className="px-3 sm:px-4 py-3 border-b border-gray-800 space-y-2">
                        <div>
                            <div className="font-medium text-sm">Conversion timeout</div>
                            <div className="text-[11px] text-gray-400 font-mono">
                                {TIMEOUT_KEY}
                            </div>
                        </div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <input
                                type="number"
                                min="0"
                                step="1"
                                value={timeoutMinutes}
                                onChange={(e) => setTimeoutMinutes(e.target.value)}
                                placeholder="off"
                                className="bg-gray-900 border border-gray-700 rounded-sm px-2 py-1 text-sm w-32 text-gray-100"
                            />
                            <span className="text-xs text-gray-400">minutes</span>
                            <button
                                type="button"
                                onClick={onTimeoutSave}
                                disabled={timeoutSaving}
                                className="bg-blue-700 hover:bg-blue-600 text-white text-xs px-3 py-1 rounded-sm disabled:opacity-50"
                            >
                                {timeoutSaving ? "Saving…" : "Save"}
                            </button>
                            {timeoutSavedAt && (
                                <span className="text-[11px] text-emerald-400">
                                    saved {Math.floor((Date.now() - timeoutSavedAt) / 1000)}s ago
                                </span>
                            )}
                        </div>
                        <div className="text-xs text-gray-400 max-w-2xl">
                            Wall-clock budget per conversion. Leave empty (or 0) for no
                            timeout. When set, the worker SIGTERMs the conversion
                            subprocess after the deadline (30 s grace, then SIGKILL).
                            The audit row gets ``conversion exceeded the configured
                            timeout of N minutes`` as its error — feeds straight into
                            the issue-bot dedup so the same converter hitting the same
                            wall doesn't spam new issues.
                        </div>
                    </div>
                )}
                {!loading && (
                    <div className="px-3 sm:px-4 py-3 border-b border-gray-800 space-y-2">
                        <div>
                            <div className="font-medium text-sm">STEP streamer threshold</div>
                            <div className="text-[11px] text-gray-400 font-mono">
                                {STREAMER_THRESHOLD_KEY}
                            </div>
                        </div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <input
                                type="number"
                                min="0"
                                step="1"
                                value={streamerThreshold}
                                onChange={(e) => setStreamerThreshold(e.target.value)}
                                placeholder="200"
                                className="bg-gray-900 border border-gray-700 rounded-sm px-2 py-1 text-sm w-32 text-gray-100"
                            />
                            <span className="text-xs text-gray-400">MB</span>
                            <button
                                type="button"
                                onClick={onStreamerThresholdSave}
                                disabled={streamerSaving}
                                className="bg-blue-700 hover:bg-blue-600 text-white text-xs px-3 py-1 rounded-sm disabled:opacity-50"
                            >
                                {streamerSaving ? "Saving…" : "Save"}
                            </button>
                            {streamerSavedAt && (
                                <span className="text-[11px] text-emerald-400">
                                    saved {Math.floor((Date.now() - streamerSavedAt) / 1000)}s ago
                                </span>
                            )}
                        </div>
                        <div className="text-xs text-gray-400 max-w-2xl">
                            On-disk STEP size above which STEP→GLB auto-routes through the
                            memory-bounded streaming reader (only when “Auto-stream large
                            STEP” is on / unset). Empty uses the code default (200 MB).
                            The OpenCASCADE loader needs several× the file size in RAM, so
                            assemblies past this point risk OOM-killing the worker pod;
                            streaming trades a small fidelity loss (skipped spherical /
                            rational-B-spline solids) for bounded memory.
                        </div>
                    </div>
                )}
                {!loading && (
                    <div className="px-3 sm:px-4 py-3 border-b border-gray-800 space-y-2">
                        <div>
                            <div className="font-medium text-sm">STEP streamer per-solid timeout</div>
                            <div className="text-[11px] text-gray-400 font-mono">
                                {SOLID_TIMEOUT_KEY}
                            </div>
                        </div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <input
                                type="number"
                                min="1"
                                step="1"
                                value={solidTimeout}
                                onChange={(e) => setSolidTimeout(e.target.value)}
                                placeholder="120"
                                className="bg-gray-900 border border-gray-700 rounded-sm px-2 py-1 text-sm w-32 text-gray-100"
                            />
                            <span className="text-xs text-gray-400">seconds</span>
                            <button
                                type="button"
                                onClick={onSolidTimeoutSave}
                                disabled={solidTimeoutSaving}
                                className="bg-blue-700 hover:bg-blue-600 text-white text-xs px-3 py-1 rounded-sm disabled:opacity-50"
                            >
                                {solidTimeoutSaving ? "Saving…" : "Save"}
                            </button>
                            {solidTimeoutSavedAt && (
                                <span className="text-[11px] text-emerald-400">
                                    saved {Math.floor((Date.now() - solidTimeoutSavedAt) / 1000)}s ago
                                </span>
                            )}
                        </div>
                        <div className="text-xs text-gray-400 max-w-2xl">
                            Wall-clock budget for tessellating a single solid in the streaming
                            STEP→GLB pool. A solid that overruns it (an OpenCASCADE hang in an
                            uninterruptible C call) has its worker killed and the solid skipped,
                            so one bad solid can’t freeze the whole conversion. Empty uses the
                            code default (120 s).
                        </div>
                    </div>
                )}
                {loading ? (
                    <div className="px-3 sm:px-4 py-4 text-sm text-gray-300">Loading settings…</div>
                ) : (
                    <table className="w-full text-sm">
                        <thead className="sticky top-0 bg-gray-800">
                        <tr className="text-left">
                            <th className="px-3 sm:px-4 py-2 w-[18rem]">Setting</th>
                            <th className="px-3 sm:px-4 py-2 w-[20rem]">Value</th>
                            <th className="px-3 sm:px-4 py-2">Description</th>
                        </tr>
                        </thead>
                        <tbody>
                        {ROWS.map((row) => (
                            <tr key={row.key} className="border-t border-gray-800 align-top">
                                <td className="px-3 sm:px-4 py-3">
                                    <div className="font-medium">{row.label}</div>
                                    <div className="text-[11px] text-gray-400 font-mono">{row.key}</div>
                                    <div className="text-[11px] text-gray-500 mt-1">
                                        Code default: <span className="font-mono">{row.codeDefault ? "true" : "false"}</span>
                                    </div>
                                </td>
                                <td className="px-3 sm:px-4 py-3">
                                    <TriSelect
                                        value={values[row.key]}
                                        onChange={(next) => onChange(row.key, next)}
                                        disabled={Boolean(saving[row.key])}
                                    />
                                </td>
                                <td className="px-3 sm:px-4 py-3 text-xs text-gray-300">
                                    {row.description}
                                </td>
                            </tr>
                        ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
};

const TriSelect: React.FC<{
    value: TriState;
    onChange: (next: TriState) => void;
    disabled?: boolean;
}> = ({value, onChange, disabled}) => {
    const opt = (v: TriState, label: string) => (
        <button
            key={v}
            onClick={() => onChange(v)}
            disabled={disabled}
            className={
                "px-3 py-1 text-xs border " +
                (value === v
                    ? "bg-blue-700 text-white border-blue-500"
                    : "bg-gray-800 text-gray-200 border-gray-700 hover:bg-gray-700")
            }
        >
            {label}
        </button>
    );
    return (
        <div className="inline-flex rounded-sm overflow-hidden">
            {opt("unset", "Unset")}
            {opt("on", "On")}
            {opt("off", "Off")}
        </div>
    );
};

export default ConversionSettingsTab;
