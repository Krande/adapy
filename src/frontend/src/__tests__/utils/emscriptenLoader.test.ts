import assert from "node:assert/strict";
import test from "node:test";

import {resolveModuleUrl} from "@/utils/wasm/emscriptenLoader";

// A worker bundled inline runs from a blob: URL, which a root-relative module
// specifier cannot be resolved against; the loader makes it absolute first.

test("a root-relative module URL is made absolute against the origin", () => {
    assert.equal(resolveModuleUrl("/wasm/adacpp_extrude.js", "http://127.0.0.1:8080"), "http://127.0.0.1:8080/wasm/adacpp_extrude.js");
});

test("absolute, protocol-relative and relative URLs are left alone", () => {
    assert.equal(resolveModuleUrl("https://cdn.example/x.js", "http://h"), "https://cdn.example/x.js");
    assert.equal(resolveModuleUrl("//cdn.example/x.js", "http://h"), "//cdn.example/x.js");
    assert.equal(resolveModuleUrl("./x.js", "http://h"), "./x.js");
});

test("without a usable origin the URL is passed through", () => {
    assert.equal(resolveModuleUrl("/wasm/x.js", undefined), "/wasm/x.js");
    assert.equal(resolveModuleUrl("/wasm/x.js", "null"), "/wasm/x.js");
});
