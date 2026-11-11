// A buffer view of a GLB read without a scene library, and the ids of a large group read through it.
import assert from "node:assert/strict";
import {test} from "node:test";

import {useLineageStore} from "@/state/lineageStore";
import {glbBufferViews} from "@/utils/glb/glbBufferView";
import {registerLineageFromExtension} from "@/utils/lineage/registerLineageFromExtension";

/** A GLB with a JSON chunk and one binary chunk. */
function glb(json: unknown, bin: Uint8Array): Uint8Array {
    let text = new TextEncoder().encode(JSON.stringify(json));
    const pad = (4 - (text.length % 4)) % 4;
    text = Uint8Array.from([...text, ...new Array(pad).fill(0x20)]);
    const out = new Uint8Array(12 + 8 + text.length + 8 + bin.length);
    const view = new DataView(out.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, out.length, true);
    view.setUint32(12, text.length, true);
    view.setUint32(16, 0x4e4f534a, true);
    out.set(text, 20);
    view.setUint32(20 + text.length, bin.length, true);
    view.setUint32(24 + text.length, 0x004e4942, true);
    out.set(bin, 28 + text.length);
    return out;
}

const ids = new Uint8Array(new Uint32Array([7, 8, 9]).buffer);
const file = glb({bufferViews: [{buffer: 0, byteOffset: 0, byteLength: 4}, {buffer: 0, byteOffset: 4, byteLength: 8}]}, ids);

test("a buffer view is the part of the binary chunk it names", () => {
    const read = glbBufferViews(file);
    assert.deepEqual([...read(1)!], [...ids.subarray(4, 12)]);
    assert.deepEqual([...read(0)!], [...ids.subarray(0, 4)]);
    assert.equal(read(2), null);
});

test("what is not a GLB has no buffer views", () => {
    assert.equal(glbBufferViews(new Uint8Array(40))(0), null);
    assert.equal(glbBufferViews(new Uint8Array(3))(0), null);
});

test("a design model and the analysis model made from it are linked without a scene object", async () => {
    useLineageStore.getState().clear();
    await registerLineageFromExtension({
        bufferView: () => null,
        extension: {assembly_guid: "A", design_objects: [{object_guids: {"Beam 1": "g1"}}]},
        fileName: "design.glb",
        root: null,
    });
    await registerLineageFromExtension({
        // Copied as the viewport's reader hands them over: a view that may start anywhere.
        bufferView: (index) => glbBufferViews(file)(index)?.slice() ?? null,
        extension: {
            assembly_guid: "A",
            simulation_objects: [{groups: [
                {parent_object_guid: "g1", members_buffer_view: 1, members_prefix: "EL"},
                {parent_object_guid: "g2", members: ["EL1"]},
            ]}],
        },
        fileName: "analysis.glb",
        root: null,
    });
    const links = useLineageStore.getState();
    assert.deepEqual(links.findLink("analysis.glb", "EL9"), {kind: "fea", cad: {file: "design.glb", name: "Beam 1", assemblyGuid: "A"}});
    assert.deepEqual(links.findLink("design.glb", "Beam 1"), {kind: "cad", fem: [{file: "analysis.glb", elementNames: ["EL8", "EL9"], assemblyGuid: "A"}]});
    assert.equal(links.findLink("analysis.glb", "EL7"), null);
    useLineageStore.getState().clear();
});
