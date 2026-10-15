// Pure helpers for the Scene > FEM panel: equipment grouped by the load cases that place it, the
// mass take-off of whatever the panel has switched on, and the resultant of a load scenario.
//
// Equipment is grouped by load case because an analysis model places the same box in several
// cases at once (self-weight and every acceleration case), and because some "equipment" exists
// only to generate a load: a GeniE wind area is a 600 t box placed in a wind case alone. Weighing
// those as mass would be wrong, so a group starts switched on only when one of its cases carries
// an acceleration (gravity or a motion) -- the cases in which equipment is weight.

import type {EquipmentGlyph, LoadGlyph, LoadScenario, MassGlyph} from "@/extensions/design_and_analysis_extension";

export type Vec3 = [number, number, number];

export interface EquipmentGroup {
    /** Stable id: the sorted load cases, joined. */
    key: string;
    /** The load cases that place every item of the group. */
    loadCases: string[];
    items: EquipmentGlyph[];
    /** kg */
    mass: number;
}

const KG_PER_TONNE = 1000;

export function equipmentGroupKey(e: EquipmentGlyph): string {
    return [...(e.load_cases ?? [])].sort().join(" · ") || "(no load case)";
}

/** Equipment grouped by the load cases placing it, the heaviest group first. */
export function equipmentGroups(equipment: readonly EquipmentGlyph[]): EquipmentGroup[] {
    const groups = new Map<string, EquipmentGroup>();
    for (const e of equipment) {
        const key = equipmentGroupKey(e);
        let g = groups.get(key);
        if (!g) {
            g = {key, loadCases: [...(e.load_cases ?? [])].sort(), items: [], mass: 0};
            groups.set(key, g);
        }
        g.items.push(e);
        g.mass += Number.isFinite(e.mass) ? e.mass : 0;
    }
    return [...groups.values()].sort((a, b) => b.mass - a.mass || a.key.localeCompare(b.key));
}

/** A group's label: its first load case, and how many more there are. */
export function equipmentGroupLabel(g: EquipmentGroup): string {
    if (g.loadCases.length === 0) return "No load case";
    return g.loadCases.length === 1 ? g.loadCases[0] : `${g.loadCases[0]} +${g.loadCases.length - 1}`;
}

/** Which groups start switched on: those placed in a case that carries an acceleration. */
export function defaultEquipmentGroups(
    groups: readonly EquipmentGroup[],
    scenarios: readonly LoadScenario[],
): Record<string, boolean> {
    const accelerated = new Set(
        scenarios.filter((s) => (s.loads ?? []).some((l) => l.type === "accel")).map((s) => s.name),
    );
    const out: Record<string, boolean> = {};
    for (const g of groups) out[g.key] = g.loadCases.some((lc) => accelerated.has(lc));
    return out;
}

/** The equipment a panel state shows: every item of every switched-on group. */
export function visibleEquipment(
    equipment: readonly EquipmentGlyph[],
    show: boolean,
    groupOn: Readonly<Record<string, boolean>>,
): EquipmentGlyph[] {
    if (!show) return [];
    return equipment.filter((e) => groupOn[equipmentGroupKey(e)] ?? false);
}

/** Where an item's mass acts: its centre of gravity, or the centre of its box without one. */
export function equipmentCog(e: EquipmentGlyph): Vec3 {
    if (e.cog) return [e.cog[0], e.cog[1], e.cog[2]];
    const local = [0, 1, 2].map((i) => (e.box_min[i] + e.box_max[i]) / 2);
    return [0, 1, 2].map(
        (k) => e.origin[k] + local[0] * e.axes[0][k] + local[1] * e.axes[1][k] + local[2] * e.axes[2][k],
    ) as Vec3;
}

export interface TakeoffRow {
    key: "model" | "masses" | "equipment";
    label: string;
    count: number;
    /** tonne */
    mass: number;
    cog: Vec3 | null;
}

export interface FemTakeoff {
    rows: TakeoffRow[];
    /** tonne */
    total: number;
    cog: Vec3 | null;
}

class Accumulator {
    mass = 0;
    moment: Vec3 = [0, 0, 0];
    count = 0;

    add(cog: Vec3, mass: number) {
        if (!Number.isFinite(mass)) return;
        this.mass += mass;
        for (let i = 0; i < 3; i++) this.moment[i] += cog[i] * mass;
        this.count += 1;
    }

    cog(): Vec3 | null {
        return this.mass > 1e-12 ? (this.moment.map((m) => m / this.mass) as Vec3) : null;
    }
}

/**
 * The mass take-off of what is switched on: the model's own take-off (its steel), the point
 * masses and the equipment, each with its centre of gravity, and their sum. The model's mass is
 * in tonne, as its take-off states it; the glyphs' in kg; every row out in tonne. A `model` of
 * null (no take-off for the model) leaves that row out.
 */
export function femTakeoff(args: {
    model: {mass: number; cog: Vec3; count: number} | null;
    masses: readonly MassGlyph[];
    equipment: readonly EquipmentGlyph[];
}): FemTakeoff {
    const rows: TakeoffRow[] = [];
    const total = new Accumulator();
    if (args.model && args.model.mass > 0) {
        rows.push({key: "model", label: "Structure", count: args.model.count, mass: args.model.mass, cog: args.model.cog});
        total.add(args.model.cog, args.model.mass);
    }
    if (args.masses.length) {
        const acc = new Accumulator();
        for (const m of args.masses) acc.add([m.position[0], m.position[1], m.position[2]], m.mass / KG_PER_TONNE);
        rows.push({key: "masses", label: "Point masses", count: acc.count, mass: acc.mass, cog: acc.cog()});
        const c = acc.cog();
        if (c) total.add(c, acc.mass);
    }
    if (args.equipment.length) {
        const acc = new Accumulator();
        for (const e of args.equipment) acc.add(equipmentCog(e), e.mass / KG_PER_TONNE);
        rows.push({key: "equipment", label: "Equipment", count: acc.count, mass: acc.mass, cog: acc.cog()});
        const c = acc.cog();
        if (c) total.add(c, acc.mass);
    }
    return {rows, total: total.mass, cog: total.cog()};
}

/** The loads a scenario shows: equipment-generated ones only while those are switched on. */
export function visibleLoads(scenario: LoadScenario | null, showGenerated: boolean): LoadGlyph[] {
    const loads = scenario?.loads ?? [];
    return showGenerated ? loads : loads.filter((l) => !l.generated_by);
}

export interface Resultant {
    /** N */
    force: Vec3;
    /** Loads that carry no force of their own here: accelerations (a force only with a mass). */
    accelerations: number;
    /** Surface pressures, whose side the glyph does not record. */
    pressures: number;
}

/** The total force of point and line loads: a line load is its mean intensity times its length. */
export function scenarioResultant(loads: readonly LoadGlyph[]): Resultant {
    const force: Vec3 = [0, 0, 0];
    let accelerations = 0;
    let pressures = 0;
    for (const l of loads) {
        if (l.type === "accel") {
            accelerations += 1;
            continue;
        }
        if (l.type === "surface") {
            pressures += 1;
            continue;
        }
        if (!l.direction || l.magnitude == null) continue;
        let size = l.magnitude;
        if (l.type === "line") {
            if (!l.position || !l.end_position) continue;
            const len = Math.hypot(
                l.end_position[0] - l.position[0],
                l.end_position[1] - l.position[1],
                l.end_position[2] - l.position[2],
            );
            size = ((l.magnitude + (l.end_magnitude ?? l.magnitude)) / 2) * len;
        }
        for (let i = 0; i < 3; i++) force[i] += l.direction[i] * size;
    }
    return {force, accelerations, pressures};
}
