// The glyphs of an analysis model's concepts as plain data: masses, restrained nodes, the selected
// constraint's two sides, the selected load scenario's arrows and the placed equipment, as triangles
// and line segments a viewport's overlay layer draws (viewport/api/overlayLayer.ts), with a table of
// what a click on each one names. No three.js: both viewports draw from the same arrays.
import type {
    BcGlyph,
    ConstraintGlyph,
    EquipmentGlyph,
    LoadScenario,
    MassGlyph,
    Vec31,
} from "@/extensions/design_and_analysis_extension";
import {refKey, type FemConceptRef} from "@/utils/fem/femSelection";
// "0xrrggbb" as written (sRGB) to linear RGB in 0..1, which an overlay takes.
import {linearHex as linear} from "@/utils/scene/sceneColor";

type Rgb = readonly [number, number, number];

export const CONCEPT_COLORS = {
    mass: 0xffb300, // amber
    bc: 0xff3b30, // red: restrained nodes
    load: 0x2ecc71, // green: applied loads
    master: 0x00b0ff, // blue: independent (master) nodes of a constraint
    slave: 0xd500f9, // magenta: dependent (slave) nodes
    equipment: 0x26c6da, // teal: placed equipment
    selected: 0xfff176, // pale yellow: what the Selected Object Info panel describes
} as const;

const MASS = linear(CONCEPT_COLORS.mass);
const BC = linear(CONCEPT_COLORS.bc);
const LOAD = linear(CONCEPT_COLORS.load);
const MASTER = linear(CONCEPT_COLORS.master);
const SLAVE = linear(CONCEPT_COLORS.slave);
const EQUIPMENT = linear(CONCEPT_COLORS.equipment);
const SELECTED = linear(CONCEPT_COLORS.selected);

/** A shape around the origin, stamped once per glyph. */
interface Template {
    positions: Float32Array;
    indices: Uint32Array;
}

/** A sphere of radius 1: `around` segments about the z axis, `along` from pole to pole. */
function sphere(around: number, along: number): Template {
    const positions: number[] = [];
    const indices: number[] = [];
    for (let j = 0; j <= along; j++) {
        const phi = (j / along) * Math.PI;
        for (let i = 0; i <= around; i++) {
            const theta = (i / around) * 2 * Math.PI;
            positions.push(Math.sin(phi) * Math.cos(theta), Math.sin(phi) * Math.sin(theta), Math.cos(phi));
        }
    }
    const row = around + 1;
    for (let j = 0; j < along; j++) {
        for (let i = 0; i < around; i++) {
            const a = j * row + i, b = a + row;
            if (j > 0) indices.push(a, b, a + 1);
            if (j < along - 1) indices.push(a + 1, b, b + 1);
        }
    }
    return {positions: Float32Array.from(positions), indices: Uint32Array.from(indices)};
}

/** A diamond with its six corners at distance 1. */
const OCTAHEDRON: Template = {
    positions: Float32Array.from([1, 0, 0, -1, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 1, 0, 0, -1]),
    indices: Uint32Array.from([0, 2, 4, 2, 1, 4, 1, 3, 4, 3, 0, 4, 2, 0, 5, 1, 2, 5, 3, 1, 5, 0, 3, 5]),
};

/** A box from -0.5 to 0.5 on each axis; corner `i` has bit 0 for x, bit 1 for y, bit 2 for z. */
const BOX: Template = {
    positions: Float32Array.from([0, 1, 2, 3, 4, 5, 6, 7].flatMap((i) => [(i & 1) - 0.5, ((i >> 1) & 1) - 0.5, ((i >> 2) & 1) - 0.5])),
    indices: Uint32Array.from([0, 2, 1, 1, 2, 3, 4, 5, 6, 5, 7, 6, 0, 1, 4, 1, 5, 4, 2, 6, 3, 3, 6, 7, 0, 4, 2, 2, 4, 6, 1, 3, 5, 3, 7, 5]),
};
/** The box's twelve edges, as pairs of its corners. */
const BOX_EDGES = [0, 1, 2, 3, 4, 5, 6, 7, 0, 2, 1, 3, 4, 6, 5, 7, 0, 4, 1, 5, 2, 6, 3, 7];

/** An arrow head: its tip at z = 1, its base (radius 0.5, five sides) at z = 0. */
const CONE: Template = (() => {
    const positions = [0, 0, 1, 0, 0, 0];
    const indices: number[] = [];
    for (let i = 0; i < 5; i++) {
        const a = (i / 5) * 2 * Math.PI;
        positions.push(0.5 * Math.cos(a), 0.5 * Math.sin(a), 0);
    }
    for (let i = 0; i < 5; i++) {
        const a = 2 + i, b = 2 + ((i + 1) % 5);
        indices.push(0, a, b, 1, b, a);
    }
    return {positions: Float32Array.from(positions), indices: Uint32Array.from(indices)};
})();

const MASS_SPHERE = sphere(16, 12);
const NODE_SPHERE = sphere(10, 8);

/** Triangles being put together: every stamp adds a template's vertices, placed and coloured. */
class Shapes {
    private positions: Float32Array<ArrayBuffer> = new Float32Array(3 * 256);
    private colors: Float32Array<ArrayBuffer> = new Float32Array(3 * 256);
    private indices = new Uint32Array(3 * 256);
    private vertices = 0;
    private corners = 0;

    get empty(): boolean {
        return this.corners === 0;
    }

    /** `t` with its x, y and z axes as `ax`, `ay`, `az` (three numbers each from `at`), around `c`. */
    stamp(t: Template, cx: number, cy: number, cz: number, axes: ArrayLike<number>, rgb: Rgb): void {
        const n = t.positions.length / 3;
        if ((this.vertices + n) * 3 > this.positions.length) {
            const size = Math.max(this.positions.length * 2, (this.vertices + n) * 3);
            this.positions = grown(this.positions, size);
            this.colors = grown(this.colors, size);
        }
        if (this.corners + t.indices.length > this.indices.length) {
            const bigger = new Uint32Array(Math.max(this.indices.length * 2, this.corners + t.indices.length));
            bigger.set(this.indices);
            this.indices = bigger;
        }
        const p = this.positions, c = this.colors, src = t.positions;
        let o = this.vertices * 3;
        for (let s = 0; s < src.length; s += 3, o += 3) {
            const x = src[s], y = src[s + 1], z = src[s + 2];
            p[o] = cx + axes[0] * x + axes[3] * y + axes[6] * z;
            p[o + 1] = cy + axes[1] * x + axes[4] * y + axes[7] * z;
            p[o + 2] = cz + axes[2] * x + axes[5] * y + axes[8] * z;
            c[o] = rgb[0];
            c[o + 1] = rgb[1];
            c[o + 2] = rgb[2];
        }
        for (let i = 0; i < t.indices.length; i++) this.indices[this.corners + i] = t.indices[i] + this.vertices;
        this.vertices += n;
        this.corners += t.indices.length;
    }

    /** The same template the same size on every axis. */
    ball(t: Template, p: ArrayLike<number>, r: number, rgb: Rgb): void {
        UNIFORM[0] = UNIFORM[4] = UNIFORM[8] = r;
        this.stamp(t, p[0], p[1], p[2], UNIFORM, rgb);
    }

    mesh(opacity: number, onTop: boolean): GlyphMesh {
        return {
            positions: this.positions.slice(0, this.vertices * 3),
            colors: this.colors.slice(0, this.vertices * 3),
            indices: this.indices.slice(0, this.corners),
            opacity,
            onTop,
        };
    }
}

const UNIFORM = new Float32Array(9);

function grown(from: Float32Array, size: number): Float32Array<ArrayBuffer> {
    const to = new Float32Array(size);
    to.set(from);
    return to;
}

/** Line segments being put together: six numbers and one colour each. */
class Segments {
    readonly ends: number[] = [];
    readonly colors: number[] = [];

    add(a: ArrayLike<number>, b: ArrayLike<number>, rgb: Rgb): void {
        this.ends.push(a[0], a[1], a[2], b[0], b[1], b[2]);
        this.colors.push(rgb[0], rgb[1], rgb[2]);
    }

    lines(onTop: boolean): GlyphLines {
        return {segments: Float32Array.from(this.ends), colors: Float32Array.from(this.colors), onTop};
    }
}

export interface GlyphMesh {
    positions: Float32Array;
    indices: Uint32Array;
    /** Linear RGB per vertex. */
    colors: Float32Array;
    opacity: number;
    /** Drawn over the model: a point glyph is seen wherever it is, even inside the structure. */
    onTop: boolean;
}

export interface GlyphLines {
    segments: Float32Array;
    /** Linear RGB per segment. */
    colors: Float32Array;
    onTop: boolean;
}

/** What a click can hit, in the glyphs' own coordinates. */
export interface ConceptPicks {
    /** x, y, z and radius per ball: the point glyphs. */
    balls: Float32Array;
    ballRefs: FemConceptRef[];
    /** Two end points per segment: load arrows and loaded lines. */
    segments: Float32Array;
    segmentRefs: FemConceptRef[];
    /** Eight corners per box (corner `i`: bit 0 x, bit 1 y, bit 2 z): the equipment. */
    boxes: Float32Array;
    boxRefs: FemConceptRef[];
}

export interface ConceptGlyphs {
    meshes: GlyphMesh[];
    lines: GlyphLines[];
    picks: ConceptPicks;
}

export interface ConceptGlyphInput {
    /** Empty for a category that is switched off. */
    masses: readonly MassGlyph[];
    bcs: readonly BcGlyph[];
    /** The equipment drawn, each with its index in the model's list (what a selection names). */
    equipment: readonly {e: EquipmentGlyph; index: number}[];
    /** The scenario whose loads are drawn, with its index, or null. */
    scenario: {index: number; scenario: LoadScenario; showGenerated: boolean} | null;
    constraint: {index: number; constraint: ConstraintGlyph} | null;
    /** `refKey` of every selected concept. */
    selected: ReadonlySet<string>;
    /** The size of a glyph, in the model's units (see `glyphSize`). */
    glyph: number;
    /** Where a load on the whole model (an acceleration) is drawn from. */
    centre: readonly [number, number, number];
}

/** The size of a glyph for a model whose box has the diagonal `diagonal`; a guess with no model. */
export function glyphSize(diagonal: number | null): number {
    return (diagonal && diagonal > 0 ? diagonal : 50) * 0.012;
}

const AXES = new Float32Array(9);

/** `dir` (unit) as the z axis of a frame `length` long and `width` wide, into AXES. */
function along(dir: readonly number[], length: number, width: number): Float32Array {
    // Any direction not along `dir`, to make the two axes across it from.
    const other = Math.abs(dir[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
    let ux = dir[1] * other[2] - dir[2] * other[1], uy = dir[2] * other[0] - dir[0] * other[2], uz = dir[0] * other[1] - dir[1] * other[0];
    const ul = Math.hypot(ux, uy, uz) || 1;
    ux /= ul, uy /= ul, uz /= ul;
    const vx = dir[1] * uz - dir[2] * uy, vy = dir[2] * ux - dir[0] * uz, vz = dir[0] * uy - dir[1] * ux;
    AXES.set([ux * width, uy * width, uz * width, vx * width, vy * width, vz * width, dir[0] * length, dir[1] * length, dir[2] * length]);
    return AXES;
}

export function buildConceptGlyphs(input: ConceptGlyphInput): ConceptGlyphs {
    const {glyph, selected} = input;
    const on = (ref: FemConceptRef) => selected.has(refKey(ref));
    // The point glyphs, over the model; what is selected is added last, so it is drawn over the rest.
    const points = new Shapes();
    const hot = new Shapes();
    const arrows = new Shapes();
    const loadLines = new Segments();
    const boxes = new Shapes();
    const hotBoxes = new Shapes();
    const boxEdges = new Segments();
    const balls: number[] = [];
    const ballRefs: FemConceptRef[] = [];
    const segments: number[] = [];
    const segmentRefs: FemConceptRef[] = [];
    const corners: number[] = [];
    const boxRefs: FemConceptRef[] = [];
    const ball = (p: ArrayLike<number>, r: number, ref: FemConceptRef) => {
        balls.push(p[0], p[1], p[2], r);
        ballRefs.push(ref);
    };

    // Masses: a radius that goes with the volume, so ten times the mass reads as about twice the
    // radius and not as ten (which would dwarf the model), with a floor so a small one stays visible.
    let maxMass = 1e-9;
    for (const m of input.masses) if ((m.mass || 0) > maxMass) maxMass = m.mass || 0;
    input.masses.forEach((m, index) => {
        const ref: FemConceptRef = {kind: "mass", index};
        const picked = on(ref);
        const r = glyph * (0.5 + 0.7 * Math.cbrt((m.mass || 0) / maxMass)) * (picked ? 1.25 : 1);
        (picked ? hot : points).ball(MASS_SPHERE, m.position, r, picked ? SELECTED : MASS);
        ball(m.position, r, ref);
    });

    // A diamond per restrained node: it has no direction, so it reads as a fixed point whichever
    // way the model is turned.
    input.bcs.forEach((bc, index) => {
        const whole = on({kind: "bc", index});
        (bc.positions ?? []).forEach((p, node) => {
            const ref: FemConceptRef = {kind: "bc", index, node};
            const picked = whole || on(ref);
            (picked ? hot : points).ball(OCTAHEDRON, p, glyph * 0.7, picked ? SELECTED : BC);
            ball(p, glyph * 0.7, ref);
        });
    });

    // The selected constraint's two sides: masters as larger spheres (there are usually far fewer,
    // and they drive the relation), slaves as smaller boxes.
    if (input.constraint) {
        const {index, constraint} = input.constraint;
        const side = (pts: Vec31[], which: "master" | "slave", t: Template, size: number, r: number, rgb: Rgb) => {
            const whole = on({kind: "constraint", index, side: which});
            pts.forEach((p, node) => {
                const ref: FemConceptRef = {kind: "constraint", index, side: which, node};
                const picked = whole || on(ref);
                (picked ? hot : points).ball(t, p, size, picked ? SELECTED : rgb);
                ball(p, r, ref);
            });
        };
        side(constraint.master_positions ?? [], "master", NODE_SPHERE, glyph * 0.5, glyph * 0.5, MASTER);
        side(constraint.slave_positions ?? [], "slave", BOX, glyph * 0.42, glyph * 0.3, SLAVE);
    }

    // The selected scenario's loads: a point load or an acceleration is one arrow along the force, a
    // line load its line with an arrow at the middle, a surface load the outline of what is loaded.
    // An arrow's length goes with the load's size against the scenario's largest: one length for all
    // would hide the small ones, and a length in proportion would dwarf the model.
    if (input.scenario) {
        const {index: scenario, scenario: sc, showGenerated} = input.scenario;
        // Indexed into the scenario's full list, which is what a selection refers to.
        const loads = (sc.loads ?? []).map((l, index) => ({l, index})).filter(({l}) => showGenerated || !l.generated_by);
        let maxMag = 1e-9;
        for (const {l} of loads) maxMag = Math.max(maxMag, Math.abs(l.magnitude ?? 0));
        for (const {l, index} of loads) {
            const ref: FemConceptRef = {kind: "load", scenario, index};
            const rgb = on(ref) ? SELECTED : LOAD;
            const segment = (a: ArrayLike<number>, b: ArrayLike<number>) => {
                loadLines.add(a, b, rgb);
                segments.push(a[0], a[1], a[2], b[0], b[1], b[2]);
                segmentRefs.push(ref);
            };
            if (l.type === "surface") {
                const pts = l.points ?? [];
                if (pts.length >= 2) for (let i = 0; i < pts.length; i++) segment(pts[i], pts[(i + 1) % pts.length]);
                continue;
            }
            let dir = [0, 0, -1];
            if (l.direction) {
                const d = Math.hypot(l.direction[0], l.direction[1], l.direction[2]);
                if (d > 0) dir = [l.direction[0] / d, l.direction[1] / d, l.direction[2] / d];
            }
            const length = glyph * (1.2 + 1.6 * (Math.abs(l.magnitude ?? 0) / maxMag));
            let origin: ArrayLike<number> = l.type === "accel" ? input.centre : (l.position ?? [0, 0, 0]);
            if (l.type === "line" && l.end_position) {
                const end = l.end_position;
                segment(origin, end);
                origin = [(origin[0] + end[0]) / 2, (origin[1] + end[1]) / 2, (origin[2] + end[2]) / 2];
            }
            // The shaft, then the head on its end: a third of the arrow, a fifth of it wide.
            const head = length * 0.32;
            const neck = [origin[0] + dir[0] * (length - head), origin[1] + dir[1] * (length - head), origin[2] + dir[2] * (length - head)];
            segment(origin, neck);
            arrows.stamp(CONE, neck[0], neck[1], neck[2], along(dir, head, length * 0.2), rgb);
            ball([neck[0] + dir[0] * head * 0.4, neck[1] + dir[1] * head * 0.4, neck[2] + dir[2] * head * 0.4], length * 0.12, ref);
        }
    }

    // A see-through box per placed equipment, turned by the frame it is placed in, with its edges
    // drawn so a box seen edge-on still reads. Unlike the point glyphs these stay behind what is in
    // front of them: they are volumes, and one drawn through the structure would hide what it stands on.
    for (const {e, index} of input.equipment) {
        const sx = e.box_max[0] - e.box_min[0], sy = e.box_max[1] - e.box_min[1], sz = e.box_max[2] - e.box_min[2];
        if (!(sx > 0 && sy > 0 && sz > 0)) continue;
        const ref: FemConceptRef = {kind: "equipment", index};
        const picked = on(ref);
        const [ax, ay, az] = e.axes;
        const mx = (e.box_min[0] + e.box_max[0]) / 2, my = (e.box_min[1] + e.box_max[1]) / 2, mz = (e.box_min[2] + e.box_max[2]) / 2;
        const cx = e.origin[0] + ax[0] * mx + ay[0] * my + az[0] * mz;
        const cy = e.origin[1] + ax[1] * mx + ay[1] * my + az[1] * mz;
        const cz = e.origin[2] + ax[2] * mx + ay[2] * my + az[2] * mz;
        AXES.set([ax[0] * sx, ax[1] * sx, ax[2] * sx, ay[0] * sy, ay[1] * sy, ay[2] * sy, az[0] * sz, az[1] * sz, az[2] * sz]);
        (picked ? hotBoxes : boxes).stamp(BOX, cx, cy, cz, AXES, picked ? SELECTED : EQUIPMENT);
        const at = corners.length;
        for (let s = 0; s < BOX.positions.length; s += 3) {
            const x = BOX.positions[s], y = BOX.positions[s + 1], z = BOX.positions[s + 2];
            corners.push(cx + AXES[0] * x + AXES[3] * y + AXES[6] * z, cy + AXES[1] * x + AXES[4] * y + AXES[7] * z, cz + AXES[2] * x + AXES[5] * y + AXES[8] * z);
        }
        boxRefs.push(ref);
        for (let k = 0; k < BOX_EDGES.length; k += 2) {
            const a = at + BOX_EDGES[k] * 3, b = at + BOX_EDGES[k + 1] * 3;
            boxEdges.add(corners.slice(a, a + 3), corners.slice(b, b + 3), picked ? SELECTED : EQUIPMENT);
        }
    }

    const meshes: GlyphMesh[] = [];
    if (!boxes.empty) meshes.push(boxes.mesh(0.22, false));
    if (!hotBoxes.empty) meshes.push(hotBoxes.mesh(0.4, false));
    if (!points.empty) meshes.push(points.mesh(0.85, true));
    if (!arrows.empty) meshes.push(arrows.mesh(1, true));
    if (!hot.empty) meshes.push(hot.mesh(0.95, true));
    const lines: GlyphLines[] = [];
    if (boxEdges.ends.length) lines.push(boxEdges.lines(false));
    if (loadLines.ends.length) lines.push(loadLines.lines(true));
    return {
        meshes,
        lines,
        picks: {
            balls: Float32Array.from(balls),
            ballRefs,
            segments: Float32Array.from(segments),
            segmentRefs,
            boxes: Float32Array.from(corners),
            boxRefs,
        },
    };
}

/** World points to the screen: x and y in CSS pixels from the view's corner and the depth (0 near,
 *  1 far), three numbers each; NaN behind the camera. `ViewportApi.projectToScreen` is one. */
export type ScreenProjector = (world: Float32Array, count: number, out: Float32Array) => unknown;

export interface ConceptPick {
    ref: FemConceptRef;
    /** How deep into the view the hit is: 0 at the near plane, 1 at the far one. */
    depth: number;
    /** An equipment box: a volume, which takes a click only when nothing solid is in front of it. */
    solid: boolean;
}

/** A line is hit within this many pixels, and a glyph is never a smaller target than this. */
const REACH_PX = 5;

const BOX_TRIANGLES = BOX.indices;

/**
 * The concept under a point of the view (CSS pixels from its corner), or null. `matrix` places the
 * glyphs in the world (column-major 4x4; identity when absent). The point glyphs are drawn over
 * everything, so the nearest of those wins outright; an equipment box is returned only when no
 * point glyph was hit, flagged `solid` for the caller to weigh against the model in front of it.
 */
export function pickConcept(
    picks: ConceptPicks, project: ScreenProjector, x: number, y: number, matrix?: ArrayLike<number> | null,
): ConceptPick | null {
    const place = (src: ArrayLike<number>, s: number, out: Float32Array, o: number, dx = 0, dy = 0, dz = 0) => {
        const px = src[s] + dx, py = src[s + 1] + dy, pz = src[s + 2] + dz;
        if (!matrix) {
            out[o] = px, out[o + 1] = py, out[o + 2] = pz;
            return;
        }
        out[o] = matrix[0] * px + matrix[4] * py + matrix[8] * pz + matrix[12];
        out[o + 1] = matrix[1] * px + matrix[5] * py + matrix[9] * pz + matrix[13];
        out[o + 2] = matrix[2] * px + matrix[6] * py + matrix[10] * pz + matrix[14];
    };
    let best: ConceptPick | null = null;
    const take = (ref: FemConceptRef, depth: number) => {
        if (depth >= 0 && depth <= 1 && (!best || depth < best.depth)) best = {ref, depth, solid: false};
    };

    // Balls: the centre and a point one radius along each axis, which together give the radius on
    // the screen whichever way the camera looks (the three offsets' squares add up to twice its square).
    const nb = picks.ballRefs.length;
    if (nb) {
        const world = new Float32Array(nb * 12), screen = new Float32Array(nb * 12);
        for (let b = 0; b < nb; b++) {
            const r = picks.balls[b * 4 + 3];
            place(picks.balls, b * 4, world, b * 12);
            place(picks.balls, b * 4, world, b * 12 + 3, r);
            place(picks.balls, b * 4, world, b * 12 + 6, 0, r);
            place(picks.balls, b * 4, world, b * 12 + 9, 0, 0, r);
        }
        project(world, nb * 4, screen);
        for (let b = 0; b < nb; b++) {
            const o = b * 12, cx = screen[o], cy = screen[o + 1];
            if (!(cx === cx)) continue;
            let sum = 0;
            for (let k = 3; k < 12; k += 3) {
                const ex = screen[o + k] - cx, ey = screen[o + k + 1] - cy;
                if (ex === ex) sum += ex * ex + ey * ey;
            }
            const reach = Math.max(Math.sqrt(sum / 2), REACH_PX);
            if (Math.hypot(x - cx, y - cy) <= reach) take(picks.ballRefs[b], screen[o + 2]);
        }
    }

    const ns = picks.segmentRefs.length;
    if (ns) {
        const world = new Float32Array(ns * 6), screen = new Float32Array(ns * 6);
        for (let s = 0; s < ns * 2; s++) place(picks.segments, s * 3, world, s * 3);
        project(world, ns * 2, screen);
        for (let s = 0; s < ns; s++) {
            const o = s * 6, ax = screen[o], ay = screen[o + 1], bx = screen[o + 3], by = screen[o + 4];
            if (!(ax === ax && bx === bx)) continue;
            const ux = bx - ax, uy = by - ay, len2 = ux * ux + uy * uy;
            const t = len2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * ux + (y - ay) * uy) / len2)) : 0;
            if (Math.hypot(x - ax - ux * t, y - ay - uy * t) <= REACH_PX) take(picks.segmentRefs[s], screen[o + 2] + (screen[o + 5] - screen[o + 2]) * t);
        }
    }
    if (best) return best;

    // Boxes: the cursor inside one of a box's faces on the screen. Depth runs straight across a
    // face there, so the weights of its three corners give the depth under the cursor.
    const nx = picks.boxRefs.length;
    let solid: ConceptPick | null = null;
    if (nx) {
        const world = new Float32Array(nx * 24), screen = new Float32Array(nx * 24);
        for (let c = 0; c < nx * 8; c++) place(picks.boxes, c * 3, world, c * 3);
        project(world, nx * 8, screen);
        for (let b = 0; b < nx; b++) {
            const base = b * 24;
            for (let t = 0; t < BOX_TRIANGLES.length; t += 3) {
                const i = base + BOX_TRIANGLES[t] * 3, j = base + BOX_TRIANGLES[t + 1] * 3, k = base + BOX_TRIANGLES[t + 2] * 3;
                const x0 = screen[i], y0 = screen[i + 1], x1 = screen[j], y1 = screen[j + 1], x2 = screen[k], y2 = screen[k + 1];
                const area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
                if (!(area === area) || area === 0) continue;
                const w1 = ((x - x0) * (y2 - y0) - (x2 - x0) * (y - y0)) / area;
                const w2 = ((x1 - x0) * (y - y0) - (x - x0) * (y1 - y0)) / area;
                if (w1 < 0 || w2 < 0 || w1 + w2 > 1) continue;
                const depth = screen[i + 2] + (screen[j + 2] - screen[i + 2]) * w1 + (screen[k + 2] - screen[i + 2]) * w2;
                if (depth >= 0 && depth <= 1 && (!solid || depth < solid.depth)) solid = {ref: picks.boxRefs[b], depth, solid: true};
            }
        }
    }
    return solid;
}
