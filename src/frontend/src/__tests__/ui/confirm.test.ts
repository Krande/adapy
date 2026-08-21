import {test, describe, beforeEach} from "node:test";
import assert from "node:assert/strict";
import {useConfirmStore, confirm} from "@/ui/confirm";
import {scopeChangeLoss, scopeChangeConfirmRequest} from "@/utils/scope/scopeChangeRules";

describe("confirm()", () => {
    beforeEach(() => {
        // Resolve anything left pending so one test cannot leak into the next.
        useConfirmStore.getState().answer(false);
    });

    test("resolves true only on an explicit confirm", async () => {
        const p = confirm({title: "t", body: ["b"], confirmLabel: "Yes"});
        assert.ok(useConfirmStore.getState().pending, "request should be pending");
        useConfirmStore.getState().answer(true);
        assert.equal(await p, true);
        assert.equal(useConfirmStore.getState().pending, null, "pending should clear");
    });

    test("cancelling resolves false", async () => {
        const p = confirm({title: "t", body: ["b"], confirmLabel: "Yes"});
        useConfirmStore.getState().answer(false);
        assert.equal(await p, false);
    });

    test("a second request cancels the first rather than stacking dialogs", async () => {
        const first = confirm({title: "one", body: [], confirmLabel: "Yes"});
        const second = confirm({title: "two", body: [], confirmLabel: "Yes"});

        // The one the user never saw must resolve as declined — the safe answer.
        assert.equal(await first, false);
        assert.equal(useConfirmStore.getState().pending?.title, "two");

        useConfirmStore.getState().answer(true);
        assert.equal(await second, true);
    });

    test("answering with nothing pending is a no-op, not a crash", () => {
        assert.doesNotThrow(() => useConfirmStore.getState().answer(true));
    });
});

describe("scope-change guard", () => {
    test("an empty scene discards nothing, so there is nothing to ask about", () => {
        const {willDiscard, names} = scopeChangeLoss({sourceNames: new Set(), sceneGroupCount: 0});
        assert.equal(willDiscard, false);
        assert.deepEqual(names, []);
    });

    test("named sources are reported as discardable", () => {
        const {willDiscard, names} = scopeChangeLoss({
            sourceNames: new Set(["a.glb", "b.glb"]),
            sceneGroupCount: 2,
        });
        assert.equal(willDiscard, true);
        assert.deepEqual(names.sort(), ["a.glb", "b.glb"]);
    });

    test("a model in the scene with no registered source still counts", () => {
        // The case that made the first version of this guard silently do nothing: only
        // the storage browser registers source names, so ?demo=1, a .show() push over the
        // websocket, and drag-and-drop all leave the set empty with a full scene.
        const {willDiscard, names} = scopeChangeLoss({sourceNames: new Set(), sceneGroupCount: 1});
        assert.equal(willDiscard, true, "an unnamed but loaded model must still be protected");
        assert.deepEqual(names, []);
    });

    test("the nameless case gets copy that does not read as a bug", () => {
        const req = scopeChangeConfirmRequest([], "Project B");
        assert.ok(req.body.every((l) => !l.includes("undefined") && !l.includes("0 models")));
        assert.ok(req.body.some((l) => /model currently in the viewer/i.test(l)));
    });

    test("a single model is named in the prompt", () => {
        const req = scopeChangeConfirmRequest(["tower.glb"], "Project B");
        assert.match(req.title, /Project B/);
        assert.ok(
            req.body.some((l) => l.includes("tower.glb")),
            "the user should see which model they are about to lose",
        );
        assert.equal(req.tone, "danger");
    });

    test("many models are counted and listed", () => {
        const req = scopeChangeConfirmRequest(["a", "b", "c"], "Project B");
        assert.ok(req.body.some((l) => l.includes("3 models")));
        assert.ok(req.body.some((l) => l.includes("a, b, c")));
    });

    test("the confirm label says what will happen, not just 'OK'", () => {
        const req = scopeChangeConfirmRequest(["a"], "S");
        assert.match(req.confirmLabel, /unload/i);
        assert.ok(req.cancelLabel && req.cancelLabel !== "Cancel");
    });
});
