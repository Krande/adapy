import React from "react";

import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useModelState} from "@/state/modelState";
import {setBeamSolidsVisible as setBeamSolidsVisibleScene} from "@/utils/scene/handlers/load_fea_streaming";

// Scene-panel "FEM" mode: visualize the analysis inputs baked into the model —
// masses, boundary conditions, constraints, and the applied load scenarios — as toggleable
// 3D glyph overlays (drawn by FemConceptsController). The load-scenario selector
// cycles through each load case / combination and shows that one's arrows.
// Swatches for the constraint info panel. Keep in step with MASTER_COLOR /
// SLAVE_COLOR in FemConceptsController, which draws the glyphs.
const MASTER_SWATCH = "#00b0ff";
const SLAVE_SWATCH = "#d500f9";

const FemConceptsPanel = () => {
    const {
        masses, bcs, constraints, scenarios,
        showMasses, showBcs, selectedScenario, selectedConstraint,
        setShowMasses, setShowBcs, setSelectedScenario, setSelectedConstraint,
    } = useFemConceptsStore();
    const hasModel = !!useModelState((s) => s.boundingBox);
    // Beam-solids visibility lives in the FEA-streaming session store (the FE mesh — design
    // model or results — loads through that path). The toggle persists the flag AND flips the
    // loaded mesh's solid-beam child via the scene helper, mirroring SimulationControls.
    const beamSolidsVisible = useFeaAnimationStore((s) => s.beamSolidsVisible);
    const setBeamSolidsVisible = useFeaAnimationStore((s) => s.setBeamSolidsVisible);
    const onToggleBeamsSolid = (next: boolean) => {
        setBeamSolidsVisible(next);
        setBeamSolidsVisibleScene(next);
    };

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

    const nCon = constraints.length;
    const con = selectedConstraint >= 0 && selectedConstraint < nCon ? constraints[selectedConstraint] : null;

    return (
        <div className="p-1 text-sm">
            {!hasModel && <p className="text-xs italic mb-2">Load a model to view its FEM concepts.</p>}

            {/* Category toggles */}
            <label className="flex items-center gap-2 mb-1">
                <input type="checkbox" checked={showMasses} onChange={(e) => setShowMasses(e.target.checked)} />
                <span>Masses</span>
                <span className="ml-auto text-xs opacity-70">{masses.length}</span>
            </label>
            <label className="flex items-center gap-2 mb-1">
                <input type="checkbox" checked={showBcs} onChange={(e) => setShowBcs(e.target.checked)} />
                <span>Boundary conditions</span>
                <span className="ml-auto text-xs opacity-70">{bcs.length}</span>
            </label>
            <label className="flex items-center gap-2 mb-2" title="Render beam (line) elements as their solid cross-section geometry">
                <input type="checkbox" checked={beamSolidsVisible} onChange={(e) => onToggleBeamsSolid(e.target.checked)} />
                <span>Beams as solid</span>
            </label>

            {/* Constraint selector — picking one draws its master/slave nodes in the viewer */}
            <div className="border-t border-gray-500 pt-2 mb-2">
                <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold">Constraint</span>
                    <span className="text-xs opacity-70">{nCon ? `${nCon} total` : "none"}</span>
                </div>
                <select
                    className="w-full text-sm rounded-sm px-1 py-0.5 bg-gray-700 text-gray-100 border border-gray-600 disabled:opacity-50"
                    disabled={nCon < 1}
                    value={selectedConstraint}
                    onChange={(e) => setSelectedConstraint(parseInt(e.target.value, 10))}
                >
                    <option value={-1}>None</option>
                    {constraints.map((c, i) => (
                        <option key={i} value={i}>
                            {c.name ?? `constraint ${i + 1}`}{c.constraint_type ? ` (${c.constraint_type})` : ""}
                        </option>
                    ))}
                </select>
                {con && (
                    <div className="mt-1 text-xs bg-gray-800/60 rounded-sm p-1.5 space-y-0.5">
                        {con.constraint_type && (
                            <div className="flex justify-between gap-2">
                                <span className="opacity-70">Type</span>
                                <span>{con.constraint_type}</span>
                            </div>
                        )}
                        <div className="flex justify-between gap-2">
                            <span className="opacity-70">
                                <span style={{color: MASTER_SWATCH}}>&#9679;</span> Master nodes
                            </span>
                            <span>{con.master_positions?.length ?? 0}</span>
                        </div>
                        <div className="flex justify-between gap-2">
                            <span className="opacity-70">
                                <span style={{color: SLAVE_SWATCH}}>&#9679;</span> Slave nodes
                            </span>
                            <span>{con.slave_positions?.length ?? 0}</span>
                        </div>
                        {con.dofs?.length ? (
                            <div className="flex justify-between gap-2">
                                <span className="opacity-70">Dofs</span>
                                <span>{con.dofs.join(", ")}</span>
                            </div>
                        ) : null}
                        {con.influence_distance != null && (
                            <div className="flex justify-between gap-2">
                                <span className="opacity-70">Influence distance</span>
                                <span>{con.influence_distance}</span>
                            </div>
                        )}
                        {con.position_tolerance != null && (
                            <div className="flex justify-between gap-2">
                                <span className="opacity-70">Position tolerance</span>
                                <span>{con.position_tolerance}</span>
                            </div>
                        )}
                    </div>
                )}
            </div>

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
                        className="flex-1 text-sm rounded-sm px-1 py-0.5 bg-gray-700 text-gray-100 border border-gray-600 disabled:opacity-50"
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
