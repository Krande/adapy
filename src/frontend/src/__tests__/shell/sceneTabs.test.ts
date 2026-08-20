import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {TAB_META, tabsForMode} from "@/shell/sceneTabs";

const ids = (mode: string, ctx: Record<string, boolean> = {}) =>
    tabsForMode(TAB_META, mode, ctx as never).map((t) => t.id);

describe("Scene panel tab scoping", () => {
    test("Mesh appears in Inspect and Results only", () => {
        // Mesh quality asks whether a discretisation is good enough to trust. That is
        // Inspect and Results work — in Build you are authoring the geometry the mesh
        // will later be made FROM, so there is nothing to assess yet.
        assert.ok(ids("inspect").includes("mesh"));
        assert.ok(ids("results").includes("mesh"));
        assert.ok(!ids("build").includes("mesh"));
        assert.ok(!ids("convert").includes("mesh"));
    });

    test("unscoped tabs appear everywhere", () => {
        for (const mode of ["inspect", "results", "build", "convert"]) {
            const t = ids(mode);
            assert.ok(t.includes("model"), `${mode} lost Model`);
            assert.ok(t.includes("tools"), `${mode} lost Tools`);
            assert.ok(t.includes("clip"), `${mode} lost Clip`);
        }
    });

    test("contextual tabs still need their content", () => {
        assert.ok(!ids("inspect").includes("fem"));
        assert.ok(ids("inspect", {fem: true}).includes("fem"));
        assert.ok(!ids("results").includes("joints"));
        assert.ok(ids("results", {joints: true}).includes("joints"));
    });

    test("mode scoping and contextual availability compose", () => {
        // Both gates apply — a contextual tab that is also mode-scoped needs both. No tab
        // is currently both, so this guards the combination rather than a live case.
        const both = [{id: "x", label: "X", ctx: true, modes: ["inspect"]}] as never;
        assert.deepEqual(tabsForMode(both, "build", {x: true} as never), []);
        assert.deepEqual(tabsForMode(both, "inspect", {} as never), []);
        assert.equal(tabsForMode(both, "inspect", {x: true} as never).length, 1);
    });

    test("a mode never ends up with no tabs at all", () => {
        for (const mode of ["inspect", "results", "build", "convert"]) {
            assert.ok(ids(mode).length > 0, `${mode} has an empty Scene panel`);
        }
    });
});
