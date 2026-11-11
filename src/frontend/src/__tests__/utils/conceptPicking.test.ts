// A click on a concept's glyph, as each viewport's click handling asks for it.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {buildConceptGlyphs, type ConceptGlyphInput} from "@/utils/fem/conceptGlyphs";
import {conceptAt, setShownConcepts, takeConceptClick} from "@/utils/fem/conceptPicking";
import type {EquipmentGlyph} from "@/extensions/design_and_analysis_extension";
import type {ViewportApi} from "@/viewport/api/viewportApi";

/** A viewport looking down -z at 10 pixels per unit; depth 0.5 at z = 0, nearer for larger z. */
function viewport(): ViewportApi & {cleared: number} {
    const v = {
        cleared: 0,
        clearSelection: () => void v.cleared++,
        projectToScreen: (world: Float32Array, count: number, out: Float32Array) => {
            for (let i = 0; i < count * 3; i += 3) {
                out[i] = world[i] * 10;
                out[i + 1] = -world[i + 1] * 10;
                out[i + 2] = 0.5 - world[i + 2] * 0.01;
            }
            return {width: 200, height: 200};
        },
    };
    return v as unknown as ViewportApi & {cleared: number};
}

const input: ConceptGlyphInput = {
    masses: [{position: [0, 0, 0], mass: 1}] as ConceptGlyphInput["masses"],
    bcs: [],
    equipment: [{index: 0, e: {origin: [10, 0, 0], axes: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], box_min: [-1, -1, -1], box_max: [1, 1, 1]} as unknown as EquipmentGlyph}],
    scenario: null, constraint: null, selected: new Set(), glyph: 1, centre: [0, 0, 0],
};
const nothingUnder = async () => null;

afterEach(() => {
    useFemConceptsStore.getState().clearConceptSelection();
    useObjectInfoStore.getState().setName(null);
});

test("only the viewport that draws the glyphs answers for them", () => {
    const drawing = viewport(), other = viewport();
    setShownConcepts(drawing, buildConceptGlyphs(input).picks);
    assert.deepEqual(conceptAt(drawing, 0, 0)?.ref, {kind: "mass", index: 0});
    assert.equal(conceptAt(other, 0, 0), null);
    // Another viewport clearing its own does not take this one's away.
    setShownConcepts(other, null);
    assert.ok(conceptAt(drawing, 0, 0));
    setShownConcepts(drawing, null);
    assert.equal(conceptAt(drawing, 0, 0), null);
});

test("a click on a glyph selects the concept in place of the objects, and one beside it does not", async () => {
    const v = viewport();
    setShownConcepts(v, buildConceptGlyphs(input).picks);
    useObjectInfoStore.getState().setName("Beam 1");
    assert.equal(await takeConceptClick(v, 0, 0, false, nothingUnder), true);
    assert.deepEqual(useFemConceptsStore.getState().selection, [{kind: "mass", index: 0}]);
    assert.equal(v.cleared, 1);
    assert.equal(useObjectInfoStore.getState().name, null);
    assert.equal(useObjectInfoStore.getState().show_info_box, true);

    // Empty space: the viewport goes on to pick, and the concept selection goes.
    assert.equal(await takeConceptClick(v, 50, 50, false, nothingUnder), false);
    assert.deepEqual(useFemConceptsStore.getState().selection, []);
    setShownConcepts(v, null);
});

test("an added click joins the selection and leaves the viewport's own alone", async () => {
    const v = viewport();
    setShownConcepts(v, buildConceptGlyphs(input).picks);
    await takeConceptClick(v, 0, 0, false, nothingUnder);
    await takeConceptClick(v, 100, 0, true, nothingUnder);
    assert.deepEqual(useFemConceptsStore.getState().selection, [{kind: "mass", index: 0}, {kind: "equipment", index: 0}]);
    assert.equal(v.cleared, 1);
    // A miss with the modifier held keeps what is selected.
    assert.equal(await takeConceptClick(v, 50, 50, true, nothingUnder), false);
    assert.equal(useFemConceptsStore.getState().selection.length, 2);
    setShownConcepts(v, null);
});

test("an equipment box takes the click only when it is in front of the model there", async () => {
    const v = viewport();
    setShownConcepts(v, buildConceptGlyphs(input).picks);
    // The box's front face is at z = 1. A surface at z = 5 is in front of it: the click is the model's.
    assert.equal(await takeConceptClick(v, 100, 0, false, async () => [10, 0, 5]), false);
    assert.deepEqual(useFemConceptsStore.getState().selection, []);
    // A surface behind the box (the deck it stands on): the click is the box's.
    assert.equal(await takeConceptClick(v, 100, 0, false, async () => [10, 0, -3]), true);
    assert.deepEqual(useFemConceptsStore.getState().selection, [{kind: "equipment", index: 0}]);
    // A point glyph never asks what is under it.
    let asked = 0;
    await takeConceptClick(v, 0, 0, false, async () => (asked++, [0, 0, 50]));
    assert.equal(asked, 0);
    assert.deepEqual(useFemConceptsStore.getState().selection, [{kind: "mass", index: 0}]);
    setShownConcepts(v, null);
});
