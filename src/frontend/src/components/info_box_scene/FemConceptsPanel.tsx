import React from "react";

import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModelState} from "@/state/modelState";

// Scene-panel "FEM" mode: visualize the analysis inputs baked into the model —
// masses, boundary conditions, and the applied load scenarios — as toggleable
// 3D glyph overlays (drawn by FemConceptsController). The load-scenario selector
// cycles through each load case / combination and shows that one's arrows.
const FemConceptsPanel = () => {
    const {
        masses, bcs, scenarios,
        showMasses, showBcs, selectedScenario,
        setShowMasses, setShowBcs, setSelectedScenario,
    } = useFemConceptsStore();
    const hasModel = !!useModelState((s) => s.boundingBox);

    const nScen = scenarios.length;
    const cycle = (delta: number) => {
        if (!nScen) return;
        // wrap, with -1 ("none") as a slot before index 0
        const next = selectedScenario + delta;
        if (next < -1) setSelectedScenario(nScen - 1);
        else if (next >= nScen) setSelectedScenario(-1);
        else setSelectedScenario(next);
    };
    const current = selectedScenario >= 0 && selectedScenario < nScen ? scenarios[selectedScenario] : null;

    return (
        <div className="p-1 text-sm">
            {!hasModel && <p className="text-xs italic mb-2">Load a model to view its FEM concepts.</p>}

            {/* Category toggles */}
            <label className="flex items-center gap-2 mb-1">
                <input type="checkbox" checked={showMasses} onChange={(e) => setShowMasses(e.target.checked)} />
                <span>Masses</span>
                <span className="ml-auto text-xs opacity-70">{masses.length}</span>
            </label>
            <label className="flex items-center gap-2 mb-2">
                <input type="checkbox" checked={showBcs} onChange={(e) => setShowBcs(e.target.checked)} />
                <span>Boundary conditions</span>
                <span className="ml-auto text-xs opacity-70">{bcs.length}</span>
            </label>

            {/* Load-scenario selector */}
            <div className="border-t border-gray-500 pt-2">
                <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold">Load scenario</span>
                    <span className="text-xs opacity-70">{nScen ? `${nScen} total` : "none"}</span>
                </div>
                <div className="flex items-center gap-1">
                    <button
                        className="px-2 py-0.5 rounded-sm bg-gray-600 text-white disabled:opacity-40"
                        disabled={nScen < 1}
                        onClick={() => cycle(-1)}
                        title="Previous scenario"
                    >
                        ‹
                    </button>
                    <select
                        className="flex-1 text-sm rounded-sm px-1 py-0.5 bg-white text-black disabled:opacity-50"
                        disabled={nScen < 1}
                        value={selectedScenario}
                        onChange={(e) => setSelectedScenario(parseInt(e.target.value, 10))}
                    >
                        <option value={-1}>None</option>
                        {scenarios.map((s, i) => (
                            <option key={i} value={i}>
                                {s.kind === "combination" ? "∑ " : ""}{s.name}
                            </option>
                        ))}
                    </select>
                    <button
                        className="px-2 py-0.5 rounded-sm bg-gray-600 text-white disabled:opacity-40"
                        disabled={nScen < 1}
                        onClick={() => cycle(1)}
                        title="Next scenario"
                    >
                        ›
                    </button>
                </div>
                {current && (
                    <p className="text-xs opacity-70 mt-1">
                        {current.kind === "combination" ? "Combination" : "Load case"} — {(current.loads ?? []).length} loads
                    </p>
                )}
            </div>
        </div>
    );
};

export default FemConceptsPanel;
