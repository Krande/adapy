// A plugin's (and the Sources tab's) `loadModelFromUrl` takes the one loader that knows both
// viewports. It used to build a three.js scene itself: with the three.js-free viewport on screen
// that added nothing and still reported the model as loaded.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const src = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (rel: string) => readFileSync(join(src, rel), "utf8").replace(/\r\n/g, "\n");

test("loadModelFromUrl goes through loadModel, with the caller's headers, on either viewport", () => {
  const context = read("plugins/context.ts");
  const start = context.indexOf("async loadModelFromUrl(owner, url, opts) {");
  const end = context.indexOf("async loadModel(owner, source) {");
  assert.ok(start > 0 && end > start);
  const body = context.slice(start, end);
  assert.match(body, /import\("@\/components\/viewer\/sceneHelpers\/loadModel"\)/);
  assert.match(body, /bytes: \{ from: "url", url, headers: opts\?\.headers \}/);
  assert.match(body, /placement: "overlay"/);
  // Not the three.js loader, and no source registered behind the loader's back.
  assert.doesNotMatch(body, /setupModelLoader|registerLoadedSource/);
});

test("the loader hands a URL's headers to whichever viewport it loads into", () => {
  const loader = read("components/viewer/sceneHelpers/loadModel.ts");
  assert.match(loader, /\{from: "url"; url: string; headers\?: Record<string, string>\}/);
  assert.match(loader, /loadFrom\(source, bytes\.url, bytes\.headers, metrics\)/);
  // One function serves both: the three.js-free viewport first, else the three.js loaders.
  const loadFrom = loader.slice(loader.indexOf("async function loadFrom("), loader.indexOf("/** Stream a stored blob in"));
  assert.match(loadFrom, /isNativeViewportActive\(\)[\s\S]*showInNativeViewport\(url, requestHeaders,[\s\S]*setupModelLoaderAsync\(\{[\s\S]*requestHeaders,/);
});
