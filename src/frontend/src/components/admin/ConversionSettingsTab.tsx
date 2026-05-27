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
        key: "pcurve_drive_edge",
        label: "Drive edge from pcurve",
        description:
            "Build each OCC edge from the (pcurve, surface) pair instead of from the " +
            "3D BSpline curve, so the edge's 3D parametrization is forced consistent " +
            "with the surface. Fixes stretched-face artifacts seen on large SAT hull models.",
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
        key: "profile_conversions",
        label: "Profile conversions",
        description:
            "Run the worker fork-child under cProfile and upload the .prof to the audit " +
            "row. Adds a few % overhead and bloats audit storage; intended for short " +
            "debugging windows, not production.",
        codeDefault: false,
    },
];

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

const ConversionSettingsTab: React.FC = () => {
    const [values, setValues] = useState<Record<string, TriState>>(() =>
        Object.fromEntries(ROWS.map((r) => [r.key, "unset"])),
    );
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
