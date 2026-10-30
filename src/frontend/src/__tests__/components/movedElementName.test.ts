// An element the bake moved into its instance's own label block is named by its Abaqus instance and number.
import {test} from "node:test";
import assert from "node:assert/strict";

import {movedElementName} from "@/utils/scene/fea/elementInstances";
import type {FeaManifest} from "@/services/api/fea";

test("a moved element reads as INSTANCE.label; one that kept its number is not renamed", () => {
    const manifest = {
        mesh: {element_instances: [{name: "DECK-1", offset: 0}, {name: "BOOM-1", offset: 1000000}, {name: "JIB-1", offset: 2000000}]},
    } as unknown as FeaManifest;
    assert.equal(movedElementName(manifest, 1000057), "BOOM-1.57");
    assert.equal(movedElementName(manifest, 2000003), "JIB-1.3");
    assert.equal(movedElementName(manifest, 57), null);
    assert.equal(movedElementName({mesh: {}} as unknown as FeaManifest, 57), null);
});
