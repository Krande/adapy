import React from "react";

import {COMPUTE_MODES, useComputeStore, type ComputeMode} from "@/state/computeStore";

// What a server-side calculation (a code check, a bake) may spend: cores and memory.
//
// A REQUEST: the worker it runs on caps it at what that machine has, and a plugin echoes the
// effective values back in its job summary. So the memory field is not a slider over the browser's
// RAM -- the browser does not know what the worker has. The defaults are "no opinion".

const MODE_LABEL: Record<ComputeMode, string> = {
    interactive: "Interactive",
    balanced: "Balanced",
    throughput: "All cores",
};

const MODE_HINT: Record<ComputeMode, string> = {
    interactive: "About half the cores: the machine stays responsive while a calculation runs.",
    balanced: "Most of the cores, a few held back for the desktop. The default.",
    throughput: "Every logical core. Fastest, and the machine will feel it.",
};

/** The calculation options: how much of the machine to take, a worker cap and a memory limit. */
const ComputeOptions: React.FC = () => {
    const maxWorkers = useComputeStore((s) => s.maxWorkers);
    const memoryLimitGb = useComputeStore((s) => s.memoryLimitGb);
    const mode = useComputeStore((s) => s.mode);
    const setMaxWorkers = useComputeStore((s) => s.setMaxWorkers);
    const setMemoryLimitGb = useComputeStore((s) => s.setMemoryLimitGb);
    const setMode = useComputeStore((s) => s.setMode);
    const reset = useComputeStore((s) => s.reset);
    const cores = typeof navigator !== "undefined" ? navigator.hardwareConcurrency || 8 : 8;

    // A text field committed on blur / Enter: normalising on every keystroke fights typing "3.5".
    const [memoryDraft, setMemoryDraft] = React.useState(memoryLimitGb === null ? "" : String(memoryLimitGb));
    React.useEffect(() => {
        setMemoryDraft(memoryLimitGb === null ? "" : String(memoryLimitGb));
    }, [memoryLimitGb]);
    const commitMemory = () => {
        const text = memoryDraft.trim();
        if (!text) return setMemoryLimitGb(null);
        const n = Number.parseFloat(text.replace(",", "."));
        setMemoryLimitGb(Number.isFinite(n) && n > 0 ? n : null);
    };

    return (
        <div className="space-y-2 text-xs" data-compute-options="">
            <p className="text-gray-400">
                What a server-side calculation may use. A request: the worker caps it at what its machine has, and the run reports the values it got.
            </p>
            <div role="group" aria-label="How much of the machine to take" className="flex flex-wrap gap-1">
                {COMPUTE_MODES.map((m) => (
                    <button
                        key={m}
                        type="button"
                        title={MODE_HINT[m]}
                        aria-pressed={mode === m}
                        className={"rounded-sm px-2 py-0.5 " + (mode === m ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300 hover:bg-gray-600")}
                        onClick={() => setMode(m)}
                    >
                        {MODE_LABEL[m]}
                    </button>
                ))}
            </div>
            <div className="text-gray-400">{MODE_HINT[mode]}</div>
            <label className="flex items-center gap-2" title="Upper bound on worker processes; empty lets the mode decide">
                Worker processes
                <input
                    type="number"
                    data-option="compute-workers"
                    min={1}
                    max={cores}
                    placeholder="auto"
                    className="w-16 rounded-sm bg-gray-700 px-1 py-0.5 text-gray-100"
                    value={maxWorkers ?? ""}
                    onChange={(e) => setMaxWorkers(e.target.value === "" ? null : Number(e.target.value))}
                />
                <span className="text-gray-500">of {cores} here</span>
            </label>
            <label className="flex items-center gap-2" title="Peak resident memory the whole calculation may reach; empty is no limit">
                Memory limit (GB)
                <input
                    type="text"
                    data-option="compute-memory"
                    inputMode="decimal"
                    placeholder="no limit"
                    className="w-20 rounded-sm bg-gray-700 px-1 py-0.5 text-gray-100"
                    value={memoryDraft}
                    onChange={(e) => setMemoryDraft(e.target.value)}
                    onBlur={commitMemory}
                    onKeyDown={(e) => e.key === "Enter" && commitMemory()}
                />
            </label>
            <button type="button" className="rounded-sm bg-gray-700 px-2 py-0.5 text-gray-200 hover:bg-gray-600" title="Back to no opinion on every field" onClick={reset}>
                Reset
            </button>
        </div>
    );
};

export default ComputeOptions;
