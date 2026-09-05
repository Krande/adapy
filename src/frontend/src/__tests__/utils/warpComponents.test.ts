import assert from "node:assert/strict";
import {describe, test} from "node:test";

import {translationOffsets, warpValue} from "@/utils/scene/fea/warpComponents";
import type {FeaManifestField} from "@/services/viewerApi";

const field = (components: string[]): FeaManifestField =>
    ({
        name_canonical: "f",
        name_native: "f",
        kind: `vector${components.length}`,
        category: "displacement",
        support: "nodal",
        components,
    }) as unknown as FeaManifestField;

describe("which slots hold the translation", () => {
    test("a plain vector3 keeps the historical 0,1,2", () => {
        assert.deepEqual(translationOffsets(field(["X", "Y", "Z"])), [0, 1, 2]);
    });

    test("a 6-component vector still starts at 0", () => {
        assert.deepEqual(
            translationOffsets(field(["X", "Y", "Z", "RX", "RY", "RZ"])),
            [0, 1, 2],
        );
    });

    test("a Sesam displacement skips its leading ALL reduction", () => {
        // The bug this exists for: reading 0,1,2 here warps by (ALL, X, Y),
        // and ALL is a non-negative aggregate, so every beam flies off.
        assert.deepEqual(
            translationOffsets(field(["ALL", "X", "Y", "Z", "RX", "RY", "RZ"])),
            [1, 2, 3],
        );
    });

    test("names are matched case-insensitively", () => {
        assert.deepEqual(translationOffsets(field(["all", "x", "y", "z"])), [1, 2, 3]);
    });

    test("alternative spellings are understood", () => {
        assert.deepEqual(translationOffsets(field(["UX", "UY", "UZ"])), [0, 1, 2]);
        assert.deepEqual(translationOffsets(field(["mag", "U1", "U2", "U3"])), [1, 2, 3]);
    });

    test("a field that names no axis falls back to position", () => {
        assert.deepEqual(translationOffsets(field(["a", "b", "c"])), [0, 1, 2]);
    });

    test("partial naming is not trusted — it falls back whole", () => {
        // Half-named is not evidence the naming means what this assumes, and
        // mixing the two readings would be worse than either.
        assert.deepEqual(translationOffsets(field(["X", "b", "c"])), [0, 1, 2]);
    });

    test("a short field reports the axes it does not carry as absent", () => {
        assert.deepEqual(translationOffsets(field(["a", "b"])), [0, 1, -1]);
        assert.deepEqual(translationOffsets(field(["a"])), [0, -1, -1]);
    });

    test("stress components are not mistaken for axes", () => {
        assert.deepEqual(translationOffsets(field(["XX", "YY", "ZZ"])), [0, 1, 2]);
    });
});

describe("reading one axis", () => {
    const values = Float32Array.from([10, 1, 2, 3, 20, 4, 5, 6]);

    test("reads at base + offset", () => {
        assert.equal(warpValue(values, 0, 1), 1);
        assert.equal(warpValue(values, 4, 3), 6);
    });

    test("an absent axis reads as zero, not as slot -1", () => {
        assert.equal(warpValue(values, 4, -1), 0);
    });

    test("off the end reads as zero rather than NaN", () => {
        assert.equal(warpValue(values, 4, 99), 0);
    });
});
