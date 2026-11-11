import assert from "node:assert/strict";
import {test} from "node:test";

import type {Billboard, BillboardLayer} from "../../viewport/api/billboards";
import {NAME_LABEL_HEIGHT_PX, nameLabels, showNameLabels, type NameLabel} from "../../utils/viewer/nameLabels";

const ref = (id: string, model = "m") => ({model, id});

test("each drawn object gets one label: its name, or its id when it has none", () => {
    const names: Record<string, string> = {a: "Deck", b: ""};
    const labels = nameLabels(
        [ref("a"), ref("b"), ref("a"), ref("gone"), ref("a", "other")],
        (r) => names[r.id],
        (r) => (r.id === "gone" ? null : [1, 2, r.id === "a" ? 3 : 4]),
    );
    assert.deepEqual(labels, [
        {key: "m\na", text: "Deck", position: [1, 2, 3]},
        {key: "m\nb", text: "b", position: [1, 2, 4]},
        {key: "other\na", text: "Deck", position: [1, 2, 3]},
    ]);
});

test("the labels stop at the cap, and what is not drawn does not count towards it", () => {
    const refs = Array.from({length: 50}, (_, i) => ref(String(i)));
    const asked: string[] = [];
    const labels = nameLabels(refs, () => "n", (r) => (asked.push(r.id), Number(r.id) % 2 ? [0, 0, 0] : null), 3);
    assert.deepEqual(labels.map((l) => l.key), ["m\n1", "m\n3", "m\n5"]);
    assert.equal(asked.length, 6, "nothing is looked up past the cap");
});

function recordingLayer(): {layer: BillboardLayer; calls: string[]; shown: Map<number, Billboard>} {
    const calls: string[] = [];
    const shown = new Map<number, Billboard>();
    let next = 1;
    const layer: BillboardLayer = {
        add: (b) => (shown.set(next, b), calls.push(`add ${b.image.key}`), next++),
        update: (h, b) => (shown.set(h, b), void calls.push(`update ${h} ${b.image.key}`)),
        remove: (h) => (shown.delete(h), void calls.push(`remove ${h}`)),
        clear: () => void calls.push("clear"),
        onClick: () => () => {},
        dispose: () => void calls.push("dispose"),
    };
    return {layer, calls, shown};
}

test("showing a new set of labels touches only what changed", () => {
    const {layer, calls, shown} = recordingLayer();
    const held = new Map<string, {handle: number; label: NameLabel}>();
    const label = (key: string, text: string, z = 0): NameLabel => ({key, text, position: [0, 0, z]});

    showNameLabels(layer, held, [label("a", "Deck"), label("b", "Box")]);
    assert.deepEqual(calls, ["add text-label:Deck", "add text-label:Box"]);
    const deck = shown.get(1)!;
    assert.equal(deck.sizing, "screen");
    assert.equal(deck.height, NAME_LABEL_HEIGHT_PX);

    calls.length = 0;
    showNameLabels(layer, held, [label("a", "Deck"), label("b", "Box")]);
    assert.deepEqual(calls, [], "the same labels again: nothing to do");

    showNameLabels(layer, held, [label("a", "Deck", 5), label("c", "Mast")]);
    assert.deepEqual(calls, ["update 1 text-label:Deck", "add text-label:Mast", "remove 2"]);
    assert.deepEqual(shown.get(1)!.position, [0, 0, 5]);

    calls.length = 0;
    showNameLabels(layer, held, []);
    assert.deepEqual(calls, ["remove 1", "remove 3"]);
    assert.equal(held.size, 0);
});
