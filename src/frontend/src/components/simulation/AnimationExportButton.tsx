import React, {useRef, useState} from "react";

import ExportAnimationIcon from "../icons/ExportAnimationIcon";
import type {AnimationFormat, ExportProgress} from "@/utils/scene/fea/animationExport/exportAnimation";

const BUTTON_CLASS =
    "bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-1.5 px-3 @sm:py-2 @sm:px-4 rounded-sm";

/** "Save the animation" for the active FEA result: MP4 or GIF, with progress and cancel.
 * A time history exports every frame of the run; a static step or mode shape one
 * cycle of its deformation sweep. */
const AnimationExportButton: React.FC = () => {
    const [menuOpen, setMenuOpen] = useState(false);
    const [progress, setProgress] = useState<ExportProgress | null>(null);
    const [error, setError] = useState<string | null>(null);
    const abortRef = useRef<AbortController | null>(null);

    const run = async (format: AnimationFormat) => {
        setMenuOpen(false);
        setError(null);
        const controller = new AbortController();
        abortRef.current = controller;
        setProgress({done: 0, total: 0});
        try {
            const {exportFeaAnimation} = await import("@/utils/scene/fea/animationExport/exportAnimation");
            await exportFeaAnimation({format, signal: controller.signal, onProgress: setProgress});
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
                className={BUTTON_CLASS + (menuOpen ? " ring-2 ring-blue-300" : "")}
                onClick={() => setMenuOpen((v) => !v)}
                title="Save the animation as a video or GIF"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
            >
                <ExportAnimationIcon/>
            </button>
            {menuOpen && (
                <div
                    role="menu"
                    className="absolute bottom-full left-0 z-20 mb-1 flex flex-col overflow-hidden rounded-sm bg-gray-800 text-xs text-white shadow-lg"
                >
                    <button role="menuitem" className="px-3 py-1.5 text-left hover:bg-gray-700" onClick={() => void run("mp4")}>
                        Video (MP4)
                    </button>
                    <button role="menuitem" className="px-3 py-1.5 text-left hover:bg-gray-700" onClick={() => void run("gif")}>
                        GIF
                    </button>
                </div>
            )}
            {error && (
                <div
                    className="absolute bottom-full left-0 z-20 mb-1 w-56 rounded-sm bg-red-800 px-2 py-1 text-xs text-white"
                    onClick={() => setError(null)}
                    title="Dismiss"
                >
                    Export failed: {error}
                </div>
            )}
        </div>
    );
};

export default AnimationExportButton;
