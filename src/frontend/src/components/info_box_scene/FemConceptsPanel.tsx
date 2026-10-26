import React from "react";

import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useModelState} from "@/state/modelState";
import {useStatsStore} from "@/state/statsStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {refsOfKind, type FemConceptRef} from "@/utils/fem/femSelection";
import {
    equipmentGroupLabel,
    equipmentGroups,
    femTakeoff,
    scenarioResultant,
    visibleEquipment,
    visibleLoads,
} from "@/utils/fem/femTakeoff";
import {setDummyMassesVisible} from "@/utils/scene/fea/dummyMassVisibility";
import {setBeamSolidsVisible as setBeamSolidsVisibleScene} from "@/utils/scene/handlers/load_fea_streaming";

// Scene-panel "FEM" mode: visualize the analysis inputs baked into the model —
// masses, boundary conditions, equipment, constraints, and the applied load scenarios — as
// toggleable 3D glyph overlays (drawn by FemConceptsController). The load-scenario selector
// cycles through each load case / combination and shows that one's arrows and resultant; the
// take-off weighs the structure plus whatever masses and equipment are switched on.
// Swatches for the constraint info panel. Keep in step with MASTER_COLOR /
// SLAVE_COLOR in FemConceptsController, which draws the glyphs.
const MASTER_SWATCH = "#00b0ff";
const SLAVE_SWATCH = "#d500f9";

const EQUIPMENT_SWATCH = "#26c6da";

// "Select" beside a category: selects every item of it that is drawn and shows them in the
// Selected Object Info panel, as a click on one glyph shows that one. Inside a <label>, so it
// stops the click before the label passes it on to the checkbox.
const SelectAll: React.FC<{refs: () => FemConceptRef[]; what: string}> = ({refs, what}) => (
    <button
        type="button"
        className="text-[11px] px-1 rounded-sm border border-gray-600 opacity-80 hover:opacity-100 hover:bg-gray-700"
        title={`Select every ${what} shown (shift-click adds to the selection)`}
        onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            const picked = refs();
            if (!picked.length) return;
            useFemConceptsStore.getState().selectConcepts(picked, e.shiftKey);
            const info = useObjectInfoStore.getState();
            if (!info.show_info_box) info.toggle();
        }}
    >
        Select
    </button>
);

const fmtT = (kg: number) => `${(kg / 1000).toLocaleString("en-US", {maximumFractionDigits: 1})} t`;

const FemConceptsPanel = () => {
    const {
        masses, bcs, constraints, scenarios, equipment,
        showMasses, showBcs, showEquipment, equipmentGroupOn, showGeneratedLoads, takeoffModel,
        selectedScenario, selectedConstraint, dummyMassIds, showDummyMasses,
        setShowMasses, setShowBcs, setShowEquipment, setEquipmentGroupOn, setShowGeneratedLoads, setTakeoffModel,
        setSelectedScenario, setSelectedConstraint,
    } = useFemConceptsStore();
    const hasModel = !!useModelState((s) => s.boundingBox);
    const stats = useStatsStore((s) => s.stats);
    const groups = React.useMemo(() => equipmentGroups(equipment), [equipment]);
    const [groupsOpen, setGroupsOpen] = React.useState(false);
    const massesKg = React.useMemo(() => masses.reduce((s, m) => s + (m.mass || 0), 0), [masses]);
    const shownEquipment = React.useMemo(
        () => visibleEquipment(equipment, showEquipment, equipmentGroupOn),
        [equipment, showEquipment, equipmentGroupOn],
    );
    const shownSet = React.useMemo(() => new Set(shownEquipment), [shownEquipment]);
    const conceptData = {masses, bcs, constraints, scenarios, equipment};
    const takeoff = React.useMemo(
        () =>
            femTakeoff({
                model: takeoffModel && stats ? {mass: stats.total_mass, cog: stats.total_cog, count: stats.objects} : null,
                masses: showMasses ? masses : [],
                equipment: shownEquipment,
            }),
        [takeoffModel, stats, showMasses, masses, shownEquipment],
    );
    // Beam-solids visibility lives in the FEA-streaming session store (the FE mesh — design
    // model or results — loads through that path). The toggle persists the flag AND flips the
    // loaded mesh's solid-beam child via the scene helper, mirroring SimulationControls.
    const beamSolidsVisible = useFeaAnimationStore((s) => s.beamSolidsVisible);
    const setBeamSolidsVisible = useFeaAnimationStore((s) => s.setBeamSolidsVisible);
    // Set when the loaded result names beam solids the viewer could not build.
    const beamSolidsUnavailable = useFeaAnimationStore((s) => s.beamSolidsUnavailable);
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
    const nGenerated = (current?.loads ?? []).filter((l) => l.generated_by).length;
    const shownLoads = visibleLoads(current, showGeneratedLoads);
    const resultant = scenarioResultant(shownLoads);

    const nCon = constraints.length;
    const con = selectedConstraint >= 0 && selectedConstraint < nCon ? constraints[selectedConstraint] : null;

    return (
        <div className="p-1 text-sm">
            {!hasModel && <p className="text-xs italic mb-2">Load a model to view its FEM concepts.</p>}

            {/* Category toggles */}
            <label className="flex items-center gap-2 mb-1">
                <input type="checkbox" checked={showMasses} onChange={(e) => setShowMasses(e.target.checked)} />
                <span>Masses</span>
                <span className="ml-auto text-xs opacity-70">
                    {masses.length}{masses.length ? ` · ${fmtT(massesKg)}` : ""}
                </span>
                {showMasses && masses.length > 0 && <SelectAll what="mass" refs={() => refsOfKind(conceptData, "mass")}/>}
            </label>
            <label className="flex items-center gap-2 mb-1">
                <input type="checkbox" checked={showBcs} onChange={(e) => setShowBcs(e.target.checked)} />
                <span>Boundary conditions</span>
                <span className="ml-auto text-xs opacity-70">{bcs.length}</span>
                {showBcs && bcs.length > 0 && <SelectAll what="support" refs={() => refsOfKind(conceptData, "bc")}/>}
            </label>
            {equipment.length > 0 && (
                <div className="mb-1">
                    <div className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            aria-label="Equipment"
                            checked={showEquipment}
                            onChange={(e) => setShowEquipment(e.target.checked)}
                        />
                        <button
                            type="button"
                            className="flex items-center gap-1"
                            aria-expanded={groupsOpen}
                            onClick={() => setGroupsOpen(!groupsOpen)}
                            title="Equipment by the load cases that place it"
                        >
                            <span style={{color: EQUIPMENT_SWATCH}}>&#9632;</span>
                            <span>Equipment</span>
                            <span className="text-xs opacity-60">{groupsOpen ? "▾" : "▸"}</span>
                        </button>
                        <span className="ml-auto text-xs opacity-70">
                            {shownEquipment.length}/{equipment.length}
                        </span>
                        {shownEquipment.length > 0 && (
                            <SelectAll
                                what="equipment"
                                refs={() => refsOfKind(conceptData, "equipment", {equipmentShown: (e) => shownSet.has(e)})}
                            />
                        )}
                    </div>
                    {groupsOpen && (
                        <div className="ml-5 mt-1 space-y-0.5">
                            {groups.map((g) => (
                                <label
                                    key={g.key}
                                    className={`flex items-center gap-2 text-xs${showEquipment ? "" : " opacity-50"}`}
                                    title={g.loadCases.join(", ")}
                                >
                                    <input
                                        type="checkbox"
                                        checked={equipmentGroupOn[g.key] ?? false}
                                        onChange={(e) => setEquipmentGroupOn(g.key, e.target.checked)}
                                    />
                                    <span className="truncate">{equipmentGroupLabel(g)}</span>
                                    <span className="ml-auto whitespace-nowrap opacity-70">
                                        {g.items.length} · {fmtT(g.mass)}
                                    </span>
                                </label>
                            ))}
                            <p className="text-[11px] opacity-60">
                                Grouped by the load cases that place them. Groups placed only in cases without an
                                acceleration (wind areas, say) start off: their mass is not weight.
                            </p>
                        </div>
                    )}
                </div>
            )}
            <label
                className={`flex items-center gap-2 mb-2${beamSolidsUnavailable ? " opacity-50" : ""}`}
                title={beamSolidsUnavailable
                    ? `Beam solids are not available: ${beamSolidsUnavailable}`
                    : "Render beam (line) elements as their solid cross-section geometry"}
            >
                <input
                    type="checkbox"
                    checked={!beamSolidsUnavailable && beamSolidsVisible}
                    disabled={!!beamSolidsUnavailable}
                    onChange={(e) => onToggleBeamsSolid(e.target.checked)}
                />
                <span>Beams as solid</span>
            </label>
            {dummyMassIds.length > 0 && (
                <label
                    className="flex items-center gap-2 mb-2"
                    title="The beams GeniE hangs an equipment mass off the deck with, from each footprint node to the mass at its centre of gravity. Not structure; drawn as solids only."
                >
                    <input
                        type="checkbox"
                        checked={showDummyMasses}
                        onChange={(e) => setDummyMassesVisible(e.target.checked)}
                    />
                    <span>Dummy masses</span>
                    <span className="ml-auto text-xs opacity-70">{dummyMassIds.length}</span>
                </label>
            )}

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
                    <div className="flex items-center gap-2 text-xs mt-1">
                        <span className="opacity-70">
                            {current.kind === "combination" ? "Combination" : "Load case"} — {(current.loads ?? []).length} loads
                        </span>
                        {shownLoads.length > 0 && (
                            <span className="ml-auto">
                                <SelectAll
                                    what="load"
                                    refs={() =>
                                        refsOfKind(conceptData, "load", {scenario: selectedScenario, showGeneratedLoads})
                                    }
                                />
                            </span>
                        )}
                    </div>
                )}
                {nGenerated > 0 && (
                    <label
                        className="flex items-center gap-2 mt-1 text-xs"
                        title="Loads the analysis model generated from the equipment placed in this case"
                    >
                        <input
                            type="checkbox"
                            checked={showGeneratedLoads}
                            onChange={(e) => setShowGeneratedLoads(e.target.checked)}
                        />
                        <span>Equipment loads</span>
                        <span className="ml-auto opacity-70">{nGenerated}</span>
                    </label>
                )}
                {current && shownLoads.length > 0 && (
                    <div className="mt-1 text-xs bg-gray-800/60 rounded-sm p-1.5">
                        <div className="flex justify-between gap-2">
                            <span className="opacity-70">Resultant (kN)</span>
                            <span className="font-mono tabular-nums">
                                {resultant.force.map((f) => (f / 1000).toFixed(1)).join(", ")}
                            </span>
                        </div>
                        {(resultant.accelerations > 0 || resultant.pressures > 0) && (
                            <div className="opacity-60 mt-0.5">
                                Point and line loads only
                                {resultant.accelerations > 0 && ` · ${resultant.accelerations} acceleration field(s) not a force here`}
                                {resultant.pressures > 0 && ` · ${resultant.pressures} pressure(s) left out`}
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Take-off of what is switched on */}
            <div className="border-t border-gray-500 pt-2 mt-2">
                <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold">Take-off</span>
                    <label className="flex items-center gap-1 text-xs" title="Count the model's own structural mass">
                        <input type="checkbox" checked={takeoffModel} onChange={(e) => setTakeoffModel(e.target.checked)} />
                        <span>Structure</span>
                    </label>
                </div>
                {takeoff.rows.length === 0 ? (
                    <p className="text-xs italic opacity-70">
                        Nothing to weigh: switch on the structure, masses or equipment.
                    </p>
                ) : (
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="opacity-60">
                                <th className="text-left font-normal">Item</th>
                                <th className="text-right font-normal">Mass (t)</th>
                                <th className="text-right font-normal">COG x, y, z (m)</th>
                            </tr>
                        </thead>
                        <tbody className="font-mono tabular-nums">
                            {takeoff.rows.map((r) => (
                                <tr key={r.key}>
                                    <td className="font-sans">
                                        {r.label} <span className="opacity-60">({r.count})</span>
                                    </td>
                                    <td className="text-right">{r.mass.toFixed(1)}</td>
                                    <td className="text-right">{fmtCog(r.cog)}</td>
                                </tr>
                            ))}
                            <tr className="border-t border-gray-600 font-semibold">
                                <td className="font-sans">Total</td>
                                <td className="text-right">{takeoff.total.toFixed(1)}</td>
                                <td className="text-right">{fmtCog(takeoff.cog)}</td>
                            </tr>
                        </tbody>
                    </table>
                )}
                {takeoffModel && !stats && (
                    <p className="text-[11px] opacity-60 mt-1">The model has no take-off of its own; its structure is not counted.</p>
                )}
            </div>
        </div>
    );
};

function fmtCog(c: [number, number, number] | null): string {
    return c ? c.map((v) => v.toFixed(2)).join(", ") : "—";
}

export default FemConceptsPanel;
