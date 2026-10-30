// The helpers on `@/viewer-core/app` a result plugin reads and formats its data with:
// number formatting, banded colour scales, and AFEL element fields from its own sidecar.
import {test} from "node:test";
import assert from "node:assert/strict";

import type {SidecarFetcher} from "@/plugins/registry";
import {clearElemFieldBlobCache} from "@/services/feaElemFieldBlob";
import {
    readSidecarElementFieldEnvelope,
    readSidecarElementFieldStep,
    withCacheBust,
} from "@/services/sidecarElementField";
import {cssScaleColor, scaleColor, type ColorScale} from "@/utils/colorScale";
import {formatNumber} from "@/utils/format";

test("formatNumber: one ladder, thresholds and digits as options", () => {
    assert.equal(formatNumber(1234567), "1.235e+6");
    assert.equal(formatNumber(123.456), "123.5");
    assert.equal(formatNumber(12.345), "12.35");
    assert.equal(formatNumber(0), "0.00");
    assert.equal(formatNumber(0.012345), "0.0123");
    assert.equal(formatNumber(null), "-");
    assert.equal(formatNumber(Number.NaN, {empty: "—"}), "—");
    assert.equal(formatNumber(12.345, {unit: "MPa"}), "12.35 MPa");
    assert.equal(formatNumber(12.345, {unit: "-"}), "12.35");
    // A provenance-style ladder: exponent below 1e-3 too, more digits.
    const fine = {expBelow: 1e-3, largeDecimals: 2, decimals: 4, sig: 4};
    assert.equal(formatNumber(0.0001234, fine), "1.2340e-4");
    assert.equal(formatNumber(1.23456, fine), "1.2346");
});

test("scaleColor picks the band whose interval holds the value", () => {
    const scale: ColorScale = {
        bands: [
            {from: 0, color: [0, 0.7451, 1]},
            {from: 0.5, color: "#ffff00"},
            {from: 1, color: "#ff0000", label: "> 1.0"},
        ],
    };
    assert.deepEqual(scaleColor(scale, -1), [0, 0.7451, 1]); // below the lowest edge: the lowest band
    assert.deepEqual(scaleColor(scale, 0.49), [0, 0.7451, 1]);
    assert.equal(scaleColor(scale, 0.5), "#ffff00"); // edges are inclusive from below
    assert.equal(scaleColor(scale, 7), "#ff0000"); // above the top edge: the top band
    assert.equal(cssScaleColor([0, 0.5, 1]), "rgb(0, 128, 255)");
});

/** An AFEL blob: 1 KiB header (magic, version, JSON) then step-major float32 rows. */
function afel(steps: number[][]): ArrayBuffer {
    const n = steps[0].length;
    const stride = n * 4;
    const buf = new ArrayBuffer(1024 + steps.length * stride);
    const dv = new DataView(buf);
    dv.setUint32(0, 0x4c454641, true);
    dv.setUint32(4, 1, true);
    const json = new TextEncoder().encode(JSON.stringify({
        name: "uf", elem_type: "quad", n_steps: steps.length, n_elements: n, n_ips: 1, n_components: 1,
        dtype: "float32", stride_bytes: stride,
    }));
    dv.setUint32(8, json.length, true);
    new Uint8Array(buf, 12, json.length).set(json);
    steps.forEach((row, i) => new Float32Array(buf, 1024 + i * stride, n).set(row));
    return buf;
}

function sidecarOf(files: Record<string, ArrayBuffer>, opts: {honourRange: boolean}) {
    const requested: string[] = [];
    const sidecar: SidecarFetcher = {
        url: (rel) => `https://example.test/bundle/${rel}`,
        json: async () => ({}),
        bytes: async (rel, range) => {
            requested.push(rel);
            const file = files[rel.split("?")[0]];
            return range && opts.honourRange ? file.slice(range.start, range.end + 1) : file;
        },
    };
    return {sidecar, requested};
}

test("an AFEL step from a plugin sidecar, by range or from the whole blob", async () => {
    const blob = afel([[0.1, 0.5, Number.NaN], [0.9, 0.2, 1.4]]);
    const ref = {url: "capacity.uf.afel", stride_bytes: 12};
    for (const honourRange of [true, false]) {
        clearElemFieldBlobCache();
        const {sidecar} = sidecarOf({"capacity.uf.afel": blob}, {honourRange});
        const step1 = await readSidecarElementFieldStep(sidecar, ref, 1);
        assert.deepEqual(Array.from(step1).map((v) => Math.round(v * 10) / 10), [0.9, 0.2, 1.4]);
    }
});

test("the envelope over chosen steps skips missing values", async () => {
    clearElemFieldBlobCache();
    const blob = afel([[0.1, -0.8, Number.NaN], [0.9, 0.2, Number.NaN]]);
    const {sidecar} = sidecarOf({"f.afel": blob}, {honourRange: true});
    const ref = {url: "f.afel", stride_bytes: 12};
    const round = (a: Float32Array | null) => Array.from(a ?? []).map((v) => Math.round(v * 10) / 10);
    assert.deepEqual(round(await readSidecarElementFieldEnvelope(sidecar, ref, [0, 1])), [0.9, 0.2, Number.NaN]);
    assert.deepEqual(round(await readSidecarElementFieldEnvelope(sidecar, ref, [0, 1], "absmax")), [0.9, 0.8, Number.NaN]);
    assert.equal(await readSidecarElementFieldEnvelope(sidecar, ref, []), null);
});

test("withCacheBust tags every request and keeps an existing query", async () => {
    const {sidecar, requested} = sidecarOf({"a.json": new ArrayBuffer(4)}, {honourRange: true});
    const busted = withCacheBust(sidecar, "run 7");
    await busted.bytes("a.json");
    assert.deepEqual(requested, ["a.json?v=run%207"]);
    assert.equal(busted.url("x?y=1"), "https://example.test/bundle/x?y=1&v=run%207");
    assert.equal(withCacheBust(sidecar, null), sidecar);
});
