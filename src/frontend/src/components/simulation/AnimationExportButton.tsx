import React, {useCallback, useRef, useState} from "react";

import AnchoredPopover from "../common/AnchoredPopover";
import ExportAnimationIcon from "../icons/ExportAnimationIcon";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import type {ExportProgress} from "@/utils/scene/fea/animationExport/exportAnimation";
import {
    ASPECT_PRESETS,
    FPS_PRESETS,
    RESOLUTION_PRESETS,
    normaliseExportSettings,
    type ExportAspect,
    type ExportFormat,
    type ExportSettings,
} from "@/utils/scene/fea/animationExport/framePlan";

const BUTTON_CLASS =
    "bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-1.5 px-3 @sm:py-2 @sm:px-4 rounded-sm";
const SELECT_CLASS = "text-black bg-white rounded-sm px-1 py-0.5";
const STORAGE_KEY = "ada.feaAnimationExport.v1";

const ASPECT_LABELS: Record<ExportAspect, string> = {
    view: "Match view",
    "16:9": "16:9",
    "4:3": "4:3",
    "1:1": "1:1 square",
    "9:16": "9:16 portrait",
};

/** Remembered per browser; a missing or blocked storage just means the defaults. */
function loadSettings(): ExportSettings {
    try {
        const raw = window.localStorage.getItem(STORAGE_KEY);
        return normaliseExportSettings(raw ? JSON.parse(raw) : null);
    } catch {
        return normaliseExportSettings(null);
    }
}

function saveSettings(settings: ExportSettings) {
    try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
        // private window / blocked storage: settings just aren't remembered
    }
}

/** "Save the animation" for the active FEA result, with format / resolution /
 * aspect / frame rate / overlay options, progress and cancel. A time history
 * exports every frame of the run; a static step or mode shape one cycle of its
 * deformation sweep. */
const AnimationExportButton: React.FC = () => {
    const timeHistory = useFeaAnimationStore((s) => s.timeHistory);
    const [open, setOpen] = useState(false);
    const [settings, setSettings] = useState<ExportSettings>(loadSettings);
    const [progress, setProgress] = useState<ExportProgress | null>(null);
    const [error, setError] = useState<string | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    // The popover is portaled to <body> (AnchoredPopover): rendered inside the
    // Simulation drawer it was clipped by the drawer's top edge.
    const buttonRef = useRef<HTMLButtonElement>(null);
    const closeOptions = useCallback(() => setOpen(false), []);
    const clearError = useCallback(() => setError(null), []);

    const update = (patch: Partial<ExportSettings>) => {
        setSettings((prev) => {
            const next = normaliseExportSettings({...prev, ...patch});
            saveSettings(next);
            return next;
        });
    };

    const run = async () => {
        setOpen(false);
        setError(null);
        const controller = new AbortController();
        abortRef.current = controller;
        setProgress({done: 0, total: 0});
        try {
            const {exportFeaAnimation} = await import("@/utils/scene/fea/animationExport/exportAnimation");
            await exportFeaAnimation({settings, signal: controller.signal, onProgress: setProgress});
        } catch (err) {
            if (!(err instanceof DOMException && err.name === "AbortError")) {
                setError(err instanceof Error ? err.message : String(err));
            }
        } finally {
            abortRef.current = null;
            setProgress(null);
        }
    };

    if (progress) {
        return (
            <div className="flex items-center gap-2 text-xs text-white">
                <span className="tabular-nums">
                    Exporting{progress.total > 0 ? ` ${progress.done}/${progress.total}` : "…"}
                </span>
                <button
                    className="rounded-sm bg-gray-700 px-2 py-1 hover:bg-gray-600"
                    onClick={() => abortRef.current?.abort()}
                    title="Cancel the export"
                >
                    Cancel
                </button>
            </div>
        );
    }

    return (
        <div className="relative">
            <button
                ref={buttonRef}
                className={BUTTON_CLASS + (open ? " ring-2 ring-blue-300" : "")}
                onClick={() => setOpen((v) => !v)}
                title="Save the animation as a video or GIF"
                aria-haspopup="dialog"
                aria-expanded={open}
            >
                <ExportAnimationIcon/>
            </button>
            {open && (
                <AnchoredPopover
                    anchorRef={buttonRef}
                    onClose={closeOptions}
                    ariaLabel="Export animation"
                    className="flex w-60 flex-col gap-1.5 rounded-sm bg-gray-800 p-2 text-xs text-white shadow-lg"
                >
                    <label className="flex items-center justify-between gap-2">
                        <span className="text-gray-300">Format</span>
                        <select
                            className={SELECT_CLASS}
                            value={settings.format}
                            onChange={(e) => update({format: e.target.value as ExportFormat})}
                        >
                            <option value="mp4">Video (MP4)</option>
                            <option value="gif">GIF</option>
                        </select>
                    </label>
                    <label className="flex items-center justify-between gap-2">
                        <span className="text-gray-300">Resolution</span>
                        <select
                            className={SELECT_CLASS}
                            value={settings.resolution}
                            onChange={(e) => update({resolution: parseInt(e.target.value, 10)})}
                        >
                            {RESOLUTION_PRESETS[settings.format].map((r) => (
                                <option key={r} value={r}>
                                    {r === 2160 ? "2160p (4K)" : `${r}p`}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="flex items-center justify-between gap-2">
                        <span className="text-gray-300">Aspect</span>
                        <select
                            className={SELECT_CLASS}
                            value={settings.aspect}
                            onChange={(e) => update({aspect: e.target.value as ExportAspect})}
                        >
                            {ASPECT_PRESETS.map((a) => (
                                <option key={a} value={a}>
                                    {ASPECT_LABELS[a]}
                                </option>
                            ))}
                        </select>
                    </label>
                    {timeHistory && (
                        <label className="flex items-center justify-between gap-2">
                            <span className="text-gray-300">Frame rate</span>
                            <select
                                className={SELECT_CLASS}
                                value={settings.fps}
                                onChange={(e) => update({fps: parseInt(e.target.value, 10)})}
                            >
                                {FPS_PRESETS.map((f) => (
                                    <option key={f} value={f}>
                                        {f} fps
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={settings.legend}
                            onChange={(e) => update({legend: e.target.checked})}
                        />
                        <span>Legend and time</span>
                    </label>
                    <label className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            checked={settings.gizmo}
                            onChange={(e) => update({gizmo: e.target.checked})}
                        />
                        <span>Orientation gizmo</span>
                    </label>
                    <button
                        className="mt-1 rounded-sm bg-blue-700 px-2 py-1.5 font-semibold hover:bg-blue-600"
                        onClick={() => void run()}
                    >
                        Export
                    </button>
                </AnchoredPopover>
            )}
            {error && (
                <AnchoredPopover
                    anchorRef={buttonRef}
                    onClose={clearError}
                    role="alert"
                    className="w-56 rounded-sm bg-red-800 px-2 py-1 text-xs text-white"
                >
                    <div onClick={clearError} title="Dismiss">
                        Export failed: {error}
                    </div>
                </AnchoredPopover>
            )}
        </div>
    );
};

export default AnimationExportButton;
