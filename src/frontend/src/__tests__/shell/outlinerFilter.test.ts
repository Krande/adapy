import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {classifyRoot, filterRoots, kindsForMode} from "@/shell/outlinerFilter";

const ctx = (proceduralName: string | null = null) => ({
    proceduralName,
    isResult: (n: string) => /\.(sin|rmed|rst)$/i.test(n),
});

const roots = (...names: string[]) => names.map((name) => ({name}));
const nameOf = (r: {name: string}) => r.name;

describe("classifyRoot", () => {
    test("a result file is a result", () => {
        assert.equal(classifyRoot("cantilever.sin", ctx()), "result");
        assert.equal(classifyRoot("beam.rmed", ctx()), "result");
    });

    test("anything else is geometry", () => {
        assert.equal(classifyRoot("topside.ifc", ctx()), "geometry");
        assert.equal(classifyRoot("part.step", ctx()), "geometry");
    });

    test("the open procedural model is recognised by name", () => {
        assert.equal(classifyRoot("Topside module A.glb", ctx("Topside module A")), "procedural");
    });

    test("a leading slash does not defeat the match", () => {
        assert.equal(classifyRoot("/Topside module A.glb", ctx("Topside module A")), "procedural");
    });

    test("procedural beats result", () => {
        // A compiled procedural model can carry results. While you are BUILDING it, the
        // fact that it is your model matters more than that it has been analysed —
        // otherwise Build mode would stop listing the very thing you are editing.
        assert.equal(classifyRoot("MyModel.sin", ctx("MyModel")), "procedural");
    });
});

describe("kindsForMode", () => {
    test("Build lists the procedural model, Results lists results", () => {
        assert.deepEqual(kindsForMode("build"), ["procedural"]);
        assert.deepEqual(kindsForMode("results"), ["result"]);
    });

    test("Inspect lists everything — it is the base state", () => {
        assert.equal(kindsForMode("inspect"), "all");
    });

    test("an unknown mode lists everything rather than nothing", () => {
        // Failing open matters: a mode added later without a rule here should show the
        // user their models, not an empty tree.
        assert.equal(kindsForMode("something-new"), "all");
    });
});

describe("filterRoots", () => {
    test("Results hides geometry and counts what it hid", () => {
        const {shown, hidden} = filterRoots(
            roots("a.sin", "topside.ifc", "part.step"),
            nameOf,
            "results",
            ctx(),
        );
        assert.deepEqual(shown.map(nameOf), ["a.sin"]);
        assert.equal(hidden, 2);
    });

    test("Build shows only the open procedural model", () => {
        const {shown, hidden} = filterRoots(
            roots("MyModel.glb", "topside.ifc"),
            nameOf,
            "build",
            ctx("MyModel"),
        );
        assert.deepEqual(shown.map(nameOf), ["MyModel.glb"]);
        assert.equal(hidden, 1);
    });

    test("Inspect hides nothing", () => {
        const {shown, hidden} = filterRoots(roots("a.sin", "b.ifc"), nameOf, "inspect", ctx());
        assert.equal(shown.length, 2);
        assert.equal(hidden, 0);
    });

    test("showAll overrides the mode filter", () => {
        const {shown, hidden} = filterRoots(roots("a.sin", "b.ifc"), nameOf, "results", ctx(), true);
        assert.equal(shown.length, 2);
        assert.equal(hidden, 0);
    });

    test("it never filters down to nothing", () => {
        // An empty Outliner in Results, while models ARE loaded, reads as "the tree is
        // broken". The mode filter is a convenience, not a rule worth enforcing against
        // the only thing you have open.
        const {shown, hidden} = filterRoots(roots("topside.ifc", "part.step"), nameOf, "results", ctx());
        assert.equal(shown.length, 2, "falls back to showing everything");
        assert.equal(hidden, 0, "and does not claim to have hidden anything");
    });

    test("an empty scene stays empty without claiming hidden rows", () => {
        const {shown, hidden} = filterRoots([], nameOf, "results", ctx());
        assert.deepEqual(shown, []);
        assert.equal(hidden, 0);
    });
});
