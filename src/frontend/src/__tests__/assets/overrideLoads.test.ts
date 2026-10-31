import assert from "node:assert/strict";
import test from "node:test";

import {assetSourceName, mapLimited, planOverrideLoads, type NodeRef} from "@/assets/delivery";

const parent: NodeRef = {provider: "vendor", collection: "ALPHA", subject: "SITE1", revision: "r1", node: "ZONE1"};
const none = () => false;

test("each override becomes its own ref: its own subject, the parent's collection", () => {
    const plan = planOverrideLoads(parent, [{node: "GEN1", subject: "GEN1", revision: "r9"}, {node: "GEN2", subject: "SUB", revision: "r8", provider: "other"}], none);
    assert.deepEqual(plan.map((p) => p.ref), [
        {provider: "vendor", collection: "ALPHA", subject: "GEN1", revision: "r9"},
        {provider: "other", collection: "ALPHA", subject: "SUB", revision: "r8", node: "GEN2"},
    ]);
});

test("malformed, repeated, self and already-loaded entries are dropped", () => {
    const loaded = new Set([assetSourceName({provider: "vendor", collection: "ALPHA", subject: "GEN3", revision: "r1"})]);
    const plan = planOverrideLoads(
        parent,
        [
            {node: "GEN1", subject: "GEN1", revision: "r9"},
            {node: "GEN1", subject: "GEN1", revision: "r9"},
            {node: "GEN4", subject: "", revision: "r1"},
            {subject: "GEN5", revision: "r1"},
            {node: "GEN3", subject: "GEN3", revision: "r1"},
            {node: "ZONE1", subject: "SITE1", revision: "r1"},
        ],
        (n) => loaded.has(n),
    );
    assert.deepEqual(plan.map((p) => p.ref.subject), ["GEN1"]);
    assert.deepEqual(planOverrideLoads(parent, undefined, none), []);
});

test("mapLimited bounds the lanes in flight and survives a failure", async () => {
    let inFlight = 0;
    let peak = 0;
    const results = await mapLimited([1, 2, 3, 4, 5, 6], 2, async (n) => {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((r) => setTimeout(r, 1));
        inFlight -= 1;
        if (n === 3) throw new Error("boom");
        return n;
    });
    assert.equal(peak, 2);
    assert.deepEqual(results.map((r) => r.status), ["fulfilled", "fulfilled", "rejected", "fulfilled", "fulfilled", "fulfilled"]);
});
