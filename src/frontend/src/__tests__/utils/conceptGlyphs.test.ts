// The glyphs of an analysis model's concepts as plain data, and a click resolved against the same
// data: what both viewports draw and pick.
import assert from "node:assert/strict";
import {test} from "node:test";

import type {EquipmentGlyph, LoadScenario} from "@/extensions/design_and_analysis_extension";
import {buildConceptGlyphs, glyphSize, pickConcept, type ConceptGlyphInput} from "@/utils/fem/conceptGlyphs";

const NOTHING: ConceptGlyphInput = {
    masses: [], bcs: [], equipment: [], scenario: null, constraint: null, selected: new Set(), glyph: 1, centre: [0, 0, 0],
};

/** Looking down -z at 10 pixels per unit, the origin at (100, 100); depth 0.5 at z = 0, nearer for larger z. */
const project = (world: Float32Array, count: number, out: Float32Array) => {
    for (let i = 0; i < count * 3; i += 3) {
        out[i] = 100 + world[i] * 10;
        out[i + 1] = 100 - world[i + 1] * 10;
        out[i + 2] = 0.5 - world[i + 2] * 0.01;
    }
};

const box = (index: number, z: number): {e: EquipmentGlyph; index: number} => ({
    index,
    e: {name: `box ${index}`, origin: [0, 0, z], axes: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], box_min: [-1, -1, -1], box_max: [1, 1, 1]} as unknown as EquipmentGlyph,
});

test("nothing switched on draws nothing", () => {
    const glyphs = buildConceptGlyphs(NOTHING);
    assert.equal(glyphs.meshes.length, 0);
    assert.equal(glyphs.lines.length, 0);
    assert.equal(pickConcept(glyphs.picks, project, 100, 100), null);
});

test("a glyph's size goes with the model's, and has a value without a model", () => {
    assert.equal(glyphSize(100), 1.2);
    assert.equal(glyphSize(null), 0.6);
    assert.equal(glyphSize(0), 0.6);
});

test("masses are spheres over the model, the heavier the larger, the selected one in its own mesh", () => {
    const masses = [{position: [0, 0, 0], mass: 8}, {position: [10, 0, 0], mass: 1}] as ConceptGlyphInput["masses"];
    const plain = buildConceptGlyphs({...NOTHING, masses});
    assert.equal(plain.meshes.length, 1);
    assert.equal(plain.meshes[0].onTop, true);
    assert.deepEqual([...plain.picks.balls].map((v) => +v.toFixed(3)), [0, 0, 0, 1.2, 10, 0, 0, 0.85]);
    // Every index names a vertex, and every vertex has a colour.
    const m = plain.meshes[0];
    assert.equal(m.colors.length, m.positions.length);
    assert.ok(m.indices.every((i) => i < m.positions.length / 3));

    const picked = buildConceptGlyphs({...NOTHING, masses, selected: new Set(["mass:1"])});
    assert.equal(picked.meshes.length, 2);
    // Drawn last, so over the others; a quarter larger.
    assert.equal(picked.meshes[1].opacity, 0.95);
    assert.equal(+picked.picks.balls[7].toFixed(4), +(0.85 * 1.25).toFixed(4));
});

test("a click takes the nearest point glyph, by its size on the screen", () => {
    const glyphs = buildConceptGlyphs({
        ...NOTHING,
        masses: [{position: [0, 0, 0], mass: 1}, {position: [0, 0, 5], mass: 1}] as ConceptGlyphInput["masses"],
        bcs: [{positions: [[4, 0, 0], [8, 0, 0]], dofs: [1, 2, 3]}] as unknown as ConceptGlyphInput["bcs"],
    });
    // Both masses are under the cursor: the one nearer the camera (z = 5) is the one clicked.
    assert.deepEqual(pickConcept(glyphs.picks, project, 100, 100)?.ref, {kind: "mass", index: 1});
    // A mass is 1.2 units, 12 pixels: 11 pixels out is on it, 14 is not.
    assert.ok(pickConcept(glyphs.picks, project, 100, 111));
    assert.equal(pickConcept(glyphs.picks, project, 100, 114), null);
    // Each restrained node is its own glyph, named by its place in the condition.
    assert.deepEqual(pickConcept(glyphs.picks, project, 180, 100)?.ref, {kind: "bc", index: 0, node: 1});
    assert.equal(pickConcept(glyphs.picks, project, 100, 100)?.solid, false);
});

test("the glyphs' own place in the world is taken into the click", () => {
    const glyphs = buildConceptGlyphs({...NOTHING, masses: [{position: [0, 0, 0], mass: 1}] as ConceptGlyphInput["masses"]});
    const moved = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 0, 1];
    assert.equal(pickConcept(glyphs.picks, project, 100, 100, moved), null);
    assert.deepEqual(pickConcept(glyphs.picks, project, 150, 100, moved)?.ref, {kind: "mass", index: 0});
});

test("a scenario's loads are arrows and lines, clicked along their length", () => {
    const scenario = {
        name: "LC1",
        loads: [
            {type: "point", position: [0, 0, 0], direction: [0, -2, 0], magnitude: 10},
            {type: "line", position: [5, 0, 0], end_position: [9, 0, 0], direction: [0, 0, -1], magnitude: 5, generated_by: "equipment"},
            {type: "surface", points: [[0, 5, 0], [2, 5, 0], [2, 7, 0]]},
        ],
    } as unknown as LoadScenario;
    const glyphs = buildConceptGlyphs({...NOTHING, scenario: {index: 2, scenario, showGenerated: true}});
    // Arrow heads are one mesh; shafts, the loaded line and the outline are lines.
    assert.equal(glyphs.meshes.length, 1);
    assert.equal(glyphs.lines.length, 1);
    // 1 shaft + (the line and its arrow's shaft) + the outline's 3 sides.
    assert.equal(glyphs.lines[0].segments.length / 6, 6);
    // The largest load's arrow is 2.8 glyphs long, along its direction (made a unit).
    const head = glyphs.picks.balls;
    assert.ok(head[1] < -1.9 && head[1] > -2.8, `the head of the arrow is down its shaft, got y=${head[1]}`);
    assert.deepEqual(pickConcept(glyphs.picks, project, 100, 110)?.ref, {kind: "load", scenario: 2, index: 0});
    assert.deepEqual(pickConcept(glyphs.picks, project, 165, 101)?.ref, {kind: "load", scenario: 2, index: 1});
    assert.deepEqual(pickConcept(glyphs.picks, project, 110, 51)?.ref, {kind: "load", scenario: 2, index: 2});
    assert.equal(pickConcept(glyphs.picks, project, 140, 140), null);

    // Loads the analysis model generated are left out on request; the others keep their index.
    const own = buildConceptGlyphs({...NOTHING, scenario: {index: 2, scenario, showGenerated: false}});
    assert.equal(own.lines[0].segments.length / 6, 4);
    assert.deepEqual(pickConcept(own.picks, project, 110, 51)?.ref, {kind: "load", scenario: 2, index: 2});
});

test("a constraint's two sides are told apart", () => {
    const constraint = {master_positions: [[0, 0, 0]], slave_positions: [[3, 0, 0], [6, 0, 0]]} as unknown as NonNullable<ConceptGlyphInput["constraint"]>["constraint"];
    const glyphs = buildConceptGlyphs({...NOTHING, constraint: {index: 4, constraint}, selected: new Set(["constraint:4:slave:*"])});
    assert.deepEqual(pickConcept(glyphs.picks, project, 100, 100)?.ref, {kind: "constraint", index: 4, side: "master", node: 0});
    assert.deepEqual(pickConcept(glyphs.picks, project, 160, 100)?.ref, {kind: "constraint", index: 4, side: "slave", node: 1});
    // The whole selected side is drawn in the selection's mesh.
    assert.equal(glyphs.meshes.length, 2);
    assert.equal(glyphs.meshes[1].positions.length / 3, 16);
});

test("equipment is a box behind what is in front of it, and a click on it says how deep", () => {
    const glyphs = buildConceptGlyphs({...NOTHING, equipment: [box(0, 0), box(3, 10)], selected: new Set(["equipment:3"])});
    assert.equal(glyphs.meshes.length, 2);
    assert.ok(glyphs.meshes.every((m) => !m.onTop && m.opacity < 1));
    assert.equal(glyphs.lines[0].segments.length / 6, 24);
    const pick = pickConcept(glyphs.picks, project, 105, 105);
    // The box nearer the camera, at its front face (z = 11).
    assert.deepEqual(pick?.ref, {kind: "equipment", index: 3});
    assert.equal(pick?.solid, true);
    assert.equal(+pick!.depth.toFixed(4), 0.39);
    assert.equal(pickConcept(glyphs.picks, project, 130, 100), null);
    // A point glyph in the same place wins outright.
    const both = buildConceptGlyphs({...NOTHING, equipment: [box(0, 0)], masses: [{position: [0, 0, -20], mass: 1}] as ConceptGlyphInput["masses"]});
    assert.equal(pickConcept(both.picks, project, 100, 100)?.ref.kind, "mass");
});

test("an equipment's frame turns its box", () => {
    const turned = {index: 0, e: {origin: [10, 0, 0], axes: [[0, 1, 0], [-1, 0, 0], [0, 0, 1]], box_min: [0, 0, 0], box_max: [4, 1, 1]} as unknown as EquipmentGlyph};
    const glyphs = buildConceptGlyphs({...NOTHING, equipment: [turned]});
    const xs: number[] = [], ys: number[] = [];
    for (let i = 0; i < glyphs.picks.boxes.length; i += 3) xs.push(glyphs.picks.boxes[i]), ys.push(glyphs.picks.boxes[i + 1]);
    // Its length runs along world y now, from the origin it is placed at.
    assert.deepEqual([Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)], [9, 10, 0, 4]);
});
