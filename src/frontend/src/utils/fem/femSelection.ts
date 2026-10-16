// What a selected FEM concept is, and what the Selected Object Info panel says about it.
//
// A selection is a list of references into the concept arrays the FEM store holds -- not copies
// of the glyphs -- so the panel always describes the data the overlay draws, and a model reload
// (which replaces the arrays) can drop a selection that no longer means anything.

import type {
    BcGlyph,
    ConstraintGlyph,
    EquipmentGlyph,
    LoadGlyph,
    LoadScenario,
    MassGlyph,
} from "@/extensions/design_and_analysis_extension";
import {equipmentCog, femTakeoff, scenarioResultant, type Vec3} from "@/utils/fem/femTakeoff";

export type FemConceptRef =
    | {kind: "mass"; index: number}
    /** `node`: the one restrained node clicked, of the condition's many. */
    | {kind: "bc"; index: number; node?: number}
    | {kind: "equipment"; index: number}
    /** `index` is into the scenario's full `loads`, not the filtered list the overlay draws. */
    | {kind: "load"; scenario: number; index: number}
    | {kind: "constraint"; index: number; side: "master" | "slave"; node?: number};

export type FemConceptKind = FemConceptRef["kind"];

export interface FemConceptData {
    masses: readonly MassGlyph[];
    bcs: readonly BcGlyph[];
    constraints: readonly ConstraintGlyph[];
    scenarios: readonly LoadScenario[];
    equipment: readonly EquipmentGlyph[];
}

export function refKey(r: FemConceptRef): string {
    switch (r.kind) {
        case "load":
            return `load:${r.scenario}:${r.index}`;
        case "bc":
            return `bc:${r.index}:${r.node ?? "*"}`;
        case "constraint":
            return `constraint:${r.index}:${r.side}:${r.node ?? "*"}`;
        default:
            return `${r.kind}:${r.index}`;
    }
}

/** `refs` with `ref` added, or -- when it is already there -- taken out (a second shift-click). */
export function toggleRef(refs: readonly FemConceptRef[], ref: FemConceptRef): FemConceptRef[] {
    const key = refKey(ref);
    return refs.some((r) => refKey(r) === key) ? refs.filter((r) => refKey(r) !== key) : [...refs, ref];
}

/** Whether every reference still points at something in `d`. */
export function refIsValid(d: FemConceptData, r: FemConceptRef): boolean {
    switch (r.kind) {
        case "mass":
            return r.index >= 0 && r.index < d.masses.length;
        case "bc":
            return r.index >= 0 && r.index < d.bcs.length && (r.node == null || r.node < d.bcs[r.index].positions.length);
        case "equipment":
            return r.index >= 0 && r.index < d.equipment.length;
        case "load":
            return r.scenario >= 0 && r.scenario < d.scenarios.length && r.index >= 0 && r.index < (d.scenarios[r.scenario].loads ?? []).length;
        case "constraint":
            return r.index >= 0 && r.index < d.constraints.length;
    }
}

export function loadOf(d: FemConceptData, r: Extract<FemConceptRef, {kind: "load"}>): LoadGlyph | undefined {
    return d.scenarios[r.scenario]?.loads?.[r.index];
}

// ── formatting ────────────────────────────────────────────────────────────────────────────

const DOF_NAMES = ["dx", "dy", "dz", "rx", "ry", "rz"];

const num = (v: number, digits = 3) =>
    Number.isFinite(v) ? v.toLocaleString("en-US", {maximumFractionDigits: digits}) : "—";
export const fmtVec = (v: readonly number[] | null | undefined, digits = 3): string =>
    v ? `(${v.map((c) => num(c, digits)).join(", ")})` : "—";
const fmtKg = (kg: number) => `${num(kg, 1)} kg (${num(kg / 1000, 3)} t)`;
const fmtDofs = (dofs: readonly number[]) => dofs.map((d) => DOF_NAMES[d - 1] ?? String(d)).join(", ") || "none";

/** A load's magnitude in its own unit: N, N/m, Pa or m/s². */
function loadUnit(l: LoadGlyph): string {
    switch (l.type) {
        case "point":
            return "N";
        case "line":
            return "N/m";
        case "surface":
            return "Pa";
        case "accel":
            return "m/s²";
    }
}

const LOAD_KIND: Record<LoadGlyph["type"], string> = {
    point: "Point load",
    line: "Line load",
    surface: "Surface pressure",
    accel: "Acceleration field",
};

export type DetailRow = [label: string, value: string];

export interface ConceptDetail {
    kind: string;
    name: string;
    rows: DetailRow[];
}

/** What the info panel shows for one selected concept. */
export function describeRef(d: FemConceptData, r: FemConceptRef): ConceptDetail | null {
    if (!refIsValid(d, r)) return null;
    switch (r.kind) {
        case "mass": {
            const m = d.masses[r.index];
            return {
                kind: "Point mass",
                name: m.name ?? `mass ${r.index + 1}`,
                rows: [
                    ["Mass", fmtKg(m.mass)],
                    ["Position (m)", fmtVec(m.position)],
                ],
            };
        }
        case "bc": {
            const bc = d.bcs[r.index];
            const rows: DetailRow[] = [
                ["Restrained", fmtDofs(bc.dofs)],
                ["Free", fmtDofs([1, 2, 3, 4, 5, 6].filter((x) => !bc.dofs.includes(x)))],
            ];
            if (bc.bc_type) rows.unshift(["Type", bc.bc_type]);
            rows.push(["Nodes", String(bc.positions.length)]);
            if (r.node != null) rows.push(["Node position (m)", fmtVec(bc.positions[r.node])]);
            else if (bc.positions.length === 1) rows.push(["Position (m)", fmtVec(bc.positions[0])]);
            return {kind: "Boundary condition", name: bc.name ?? `support ${r.index + 1}`, rows};
        }
        case "equipment": {
            const e = d.equipment[r.index];
            const size = [0, 1, 2].map((i) => e.box_max[i] - e.box_min[i]);
            return {
                kind: "Equipment",
                name: e.name ?? `equipment ${r.index + 1}`,
                rows: [
                    ["Mass", fmtKg(e.mass)],
                    ["Size x, y, z (m)", size.map((s) => num(s)).join(" × ")],
                    ["COG (m)", fmtVec(equipmentCog(e))],
                    ["Origin (m)", fmtVec(e.origin)],
                    ["Local x", fmtVec(e.axes[0])],
                    ["Local y", fmtVec(e.axes[1])],
                    ["Local z", fmtVec(e.axes[2])],
                    ["Load cases", (e.load_cases ?? []).join(", ") || "—"],
                ],
            };
        }
        case "load": {
            const scenario = d.scenarios[r.scenario];
            const l = loadOf(d, r)!;
            const unit = loadUnit(l);
            const rows: DetailRow[] = [
                ["Kind", LOAD_KIND[l.type]],
                [scenario.kind === "combination" ? "Combination" : "Load case", scenario.name],
            ];
            if (l.generated_by) rows.push(["Generated from", `equipment ${l.generated_by}`]);
            if (l.magnitude != null) {
                rows.push([l.type === "line" ? "Intensity, start" : "Magnitude", `${num(l.magnitude, 2)} ${unit}`]);
            }
            if (l.type === "line" && l.end_magnitude != null) rows.push(["Intensity, end", `${num(l.end_magnitude, 2)} ${unit}`]);
            if (l.direction) rows.push(["Direction", fmtVec(l.direction)]);
            if (l.position && l.type !== "accel") rows.push([l.type === "line" ? "Start (m)" : "Position (m)", fmtVec(l.position)]);
            if (l.end_position) {
                rows.push(["End (m)", fmtVec(l.end_position)]);
                if (l.position) {
                    const len = Math.hypot(...[0, 1, 2].map((i) => l.end_position![i] - l.position![i]));
                    rows.push(["Length (m)", num(len)]);
                }
            }
            if (l.type === "point" || l.type === "line") {
                rows.push(["Force (kN)", fmtVec(scenarioResultant([l]).force.map((f) => f / 1000), 3)]);
            }
            if (l.moment) rows.push(["Moment (N·m)", fmtVec(l.moment, 2)]);
            if (l.type === "surface") rows.push(["Corners", String(l.points?.length ?? 0)]);
            return {kind: LOAD_KIND[l.type], name: l.name ?? `load ${r.index + 1}`, rows};
        }
        case "constraint": {
            const c = d.constraints[r.index];
            const pts = r.side === "master" ? c.master_positions : c.slave_positions;
            const rows: DetailRow[] = [
                ["Side", r.side === "master" ? "Master (independent)" : "Slave (dependent)"],
                ["Master nodes", String(c.master_positions.length)],
                ["Slave nodes", String(c.slave_positions.length)],
            ];
            if (c.constraint_type) rows.unshift(["Type", c.constraint_type]);
            if (c.dofs?.length) rows.push(["Dofs", fmtDofs(c.dofs)]);
            if (r.node != null && pts[r.node]) rows.push(["Node position (m)", fmtVec(pts[r.node])]);
            return {kind: "Constraint", name: c.name ?? `constraint ${r.index + 1}`, rows};
        }
    }
}

export interface SelectionSummary {
    counts: {kind: string; count: number}[];
    /** Mass of the selected point masses and equipment, tonne, and where it acts. */
    mass: number;
    cog: Vec3 | null;
    /** Resultant of the selected point and line loads, N. */
    force: Vec3 | null;
}

/** What a selection of several concepts adds up to. */
export function summarizeSelection(d: FemConceptData, refs: readonly FemConceptRef[]): SelectionSummary {
    const counts = new Map<string, number>();
    const masses: MassGlyph[] = [];
    const equipment: EquipmentGlyph[] = [];
    const loads: LoadGlyph[] = [];
    for (const r of refs) {
        const detail = describeRef(d, r);
        if (!detail) continue;
        counts.set(detail.kind, (counts.get(detail.kind) ?? 0) + 1);
        if (r.kind === "mass") masses.push(d.masses[r.index]);
        else if (r.kind === "equipment") equipment.push(d.equipment[r.index]);
        else if (r.kind === "load") loads.push(loadOf(d, r)!);
    }
    const weighed = femTakeoff({model: null, masses, equipment});
    const forceLoads = loads.filter((l) => l.type === "point" || l.type === "line");
    return {
        counts: [...counts].map(([kind, count]) => ({kind, count})),
        mass: weighed.total,
        cog: weighed.cog,
        force: forceLoads.length ? scenarioResultant(forceLoads).force : null,
    };
}

/** References to every item of one category: what a panel's "select all" picks. Loads are the
 *  given scenario's, and only those it currently shows. */
export function refsOfKind(
    d: FemConceptData,
    kind: "mass" | "bc" | "equipment" | "load",
    opts: {scenario?: number; showGeneratedLoads?: boolean; equipmentShown?: (e: EquipmentGlyph) => boolean} = {},
): FemConceptRef[] {
    switch (kind) {
        case "mass":
            return d.masses.map((_, index) => ({kind, index}));
        case "bc":
            return d.bcs.map((_, index) => ({kind, index}));
        case "equipment":
            return d.equipment
                .map((e, index) => ({e, index}))
                .filter(({e}) => (opts.equipmentShown ? opts.equipmentShown(e) : true))
                .map(({index}) => ({kind, index}));
        case "load": {
            const s = opts.scenario ?? -1;
            const loads = d.scenarios[s]?.loads ?? [];
            return loads
                .map((l, index) => ({l, index}))
                .filter(({l}) => opts.showGeneratedLoads !== false || !l.generated_by)
                .map(({index}) => ({kind, scenario: s, index}));
        }
    }
}

/** The loads one equipment generated in a scenario. */
export function loadsGeneratedBy(d: FemConceptData, scenario: number, equipmentName: string): FemConceptRef[] {
    return (d.scenarios[scenario]?.loads ?? [])
        .map((l, index) => ({l, index}))
        .filter(({l}) => l.generated_by === equipmentName)
        .map(({index}) => ({kind: "load" as const, scenario, index}));
}
