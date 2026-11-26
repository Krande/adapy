// A compile result used to replace the WHOLE scene (loadModel's default "replace" placement): the
// other model, a streamed result and the other LOD's result all went with it. "replaceSource"
// unloads only the earlier load of the very same source name and leaves everything else exactly
// as it was -- see components/viewer/sceneHelpers/loadModel.ts.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { mock, test } from "node:test";
import { fileURLToPath } from "node:url";

// Enough of a browser for the store modules to load under `node --test`.
await import("../fixtures/panelRealm");

const { loadModel } = await import("@/components/viewer/sceneHelpers/loadModel");
const { useModelState } = await import("@/state/modelState");
const { useColorStore } = await import("@/state/colorLegendStore");
const { capabilities } = await import("@/services/capabilities");

const src = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (rel: string) => readFileSync(join(src, rel), "utf8").replace(/\r\n/g, "\n");

const reset = () => {
  useModelState.getState().clearLoadedSources();
  useColorStore.getState().setShowLegend(false);
};

test("replaceSource unloads only its own source and keeps the rest loaded, FEA streaming intact", async () => {
  reset();
  useModelState.getState().registerLoadedSourceName("model.glb");
  useModelState.getState().registerLoadedSourceName("procedural:frame");
  // Stands in for an active streamed FEA session (clearActiveFeaStreaming resets this).
  useColorStore.getState().setShowLegend(true);
  try {
    await loadModel({
      sourceName: "procedural:frame",
      bytes: { from: "url", url: "blob:test" },
      placement: "replaceSource",
      autoFitOverride: false,
      translate: true,
    });
    const names = useModelState.getState().loadedSourceNames;
    assert.ok(names.has("model.glb"), "the other model stays loaded");
    assert.ok(names.has("procedural:frame"), "the result itself comes back under its own name");
    assert.equal(names.size, 2, "only one procedural:frame source exists");
    assert.equal(useColorStore.getState().showLegend, true, "a streamed FEA session is not ended");
  } finally {
    reset();
  }
});

test("a sim and a detail compile each replace only their own source", async () => {
  reset();
  try {
    await loadModel({
      sourceName: "procedural:frame",
      bytes: { from: "url", url: "blob:test" },
      placement: "replaceSource",
      autoFitOverride: false,
      translate: true,
    });
    await loadModel({
      sourceName: "procedural-detail:frame",
      bytes: { from: "url", url: "blob:test" },
      placement: "replaceSource",
      autoFitOverride: false,
      translate: true,
    });
    let names = useModelState.getState().loadedSourceNames;
    assert.ok(names.has("procedural:frame") && names.has("procedural-detail:frame"), "both LODs loaded");
    // Recompiling the sim LOD must not drop the detail LOD's result.
    await loadModel({
      sourceName: "procedural:frame",
      bytes: { from: "url", url: "blob:test" },
      placement: "replaceSource",
      autoFitOverride: false,
      translate: true,
    });
    names = useModelState.getState().loadedSourceNames;
    assert.ok(names.has("procedural:frame") && names.has("procedural-detail:frame"), "recompiling sim keeps detail");
    assert.equal(names.size, 2, "no stray extra source");
  } finally {
    reset();
  }
});

test("replaceSource on an empty scene falls back to a plain replace", async () => {
  reset();
  try {
    await loadModel({
      sourceName: "procedural:frame",
      bytes: { from: "url", url: "blob:test" },
      placement: "replaceSource",
      autoFitOverride: false,
      translate: true,
    });
    const names = useModelState.getState().loadedSourceNames;
    assert.equal(names.size, 1, "the first compile in an empty scene behaves as a replace");
    assert.ok(names.has("procedural:frame"));
    assert.equal(useModelState.getState().loadedSourceName, "procedural:frame");
  } finally {
    reset();
  }
});

// Two results loaded together on an EMPTY scene (e.g. superimpose) each snapshot `treatAsReplace`
// before either has registered -- without the queue in loadModel.ts both see the registry empty
// and both take the full "replace" path, and `setLoadedSourceName` ("opens a fresh session") then
// runs twice, the later one simply erasing the earlier's name even though its model is still in
// the scene (confirmed: before the fix, this left only the SECOND source's name in the registry).
test("two different-source replaceSource loads started together on an empty scene both register", async () => {
  reset();
  try {
    await Promise.all([
      loadModel({ sourceName: "a.glb", bytes: { from: "url", url: "blob:test-a" }, placement: "replaceSource", autoFitOverride: false, translate: true }),
      loadModel({ sourceName: "b.glb", bytes: { from: "url", url: "blob:test-b" }, placement: "replaceSource", autoFitOverride: false, translate: true }),
    ]);
    const names = useModelState.getState().loadedSourceNames;
    assert.ok(names.has("a.glb"), `"a.glb" must survive, got: ${[...names]}`);
    assert.ok(names.has("b.glb"), `"b.glb" must survive, got: ${[...names]}`);
    assert.equal(names.size, 2);
  } finally {
    reset();
  }
});

test("three overlapping replace/replaceSource loads all register, in some order, none lost", async () => {
  reset();
  try {
    await Promise.all([
      loadModel({ sourceName: "x.glb", bytes: { from: "url", url: "blob:test-x" }, placement: "replace", autoFitOverride: false }),
      loadModel({ sourceName: "y.glb", bytes: { from: "url", url: "blob:test-y" }, placement: "replaceSource", autoFitOverride: false, translate: true }),
      loadModel({ sourceName: "z.glb", bytes: { from: "url", url: "blob:test-z" }, placement: "replaceSource", autoFitOverride: false, translate: true }),
    ]);
    // Queued (not raced) in invocation order: x's "replace" runs first (on the still-empty scene)
    // and registers; y and z then each run their OWN "replaceSource" against a registry that
    // already has what came before, so each overlays and keeps the rest -- all three survive.
    const names = useModelState.getState().loadedSourceNames;
    assert.deepEqual([...names].sort(), ["x.glb", "y.glb", "z.glb"], `all three register, got: ${[...names]}`);
  } finally {
    reset();
  }
});

// The queue above chains every replace/replaceSource load onto whatever came before it, so that a
// rejected OR a never-settling predecessor must not freeze every later load for the rest of the
// session (loadModel.ts: QUEUE_WAIT_MS caps the wait at 30s and warns).
test("a rejected first load does not block a later one", async () => {
  reset();
  try {
    // "storage" bytes with no backend reachable (this test's capabilities stub refuses both the
    // presign and the relayed-blob verb) rejects for real, same as a 403 or a network error would.
    const first = loadModel({ sourceName: "bad.glb", bytes: { from: "storage", scope: "sc", glbKey: "k" }, placement: "replace" }).then(
      () => "resolved",
      (e) => e,
    );
    const second = loadModel({ sourceName: "good.glb", bytes: { from: "url", url: "blob:test-good" }, placement: "replace", autoFitOverride: false });
    const [firstOutcome] = await Promise.all([first, second]);
    assert.ok(firstOutcome instanceof Error, `the first load's own rejection still reaches its caller, got: ${firstOutcome}`);
    const names = useModelState.getState().loadedSourceNames;
    assert.ok(names.has("good.glb"), `the second load must still register, got: ${[...names]}`);
  } finally {
    reset();
  }
});

test("a never-settling first load is capped after ~30s (fake timers), warned, and the second still runs", async () => {
  reset();
  const filesCap = capabilities.files as unknown as { requestDownloadUrl: (...a: unknown[]) => Promise<unknown> };
  const origRequestDownloadUrl = filesCap.requestDownloadUrl;
  const origWarn = console.warn;
  const warnings: unknown[][] = [];
  // Stands in for the hang paths the review names (an object store this browser cannot reach, a
  // stalled /blobs GET): the presign call itself never settles, so loadAndRegister never does.
  filesCap.requestDownloadUrl = () => new Promise(() => {});
  console.warn = (...args: unknown[]) => void warnings.push(args);
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const hung = loadModel({ sourceName: "hang.glb", bytes: { from: "storage", scope: "sc", glbKey: "k" }, placement: "replace" });
    hung.catch(() => {}); // never settles; nothing here awaits it directly
    const second = loadModel({ sourceName: "good.glb", bytes: { from: "url", url: "blob:test-good" }, placement: "replace", autoFitOverride: false });

    // Let the hung load's own async chain (the presign call) actually start before advancing the
    // clock, then jump the fake clock past QUEUE_WAIT_MS so the second load's capped wait trips.
    await new Promise((r) => setImmediate(r));
    mock.timers.tick(30_000);
    await new Promise((r) => setImmediate(r));

    await second;
    const names = useModelState.getState().loadedSourceNames;
    assert.ok(names.has("good.glb"), `the second load must run once the cap trips, got: ${[...names]}`);
    assert.ok(
      warnings.some((args) => String(args[0]).includes("has not settled")),
      `a console.warn names the timeout, got: ${JSON.stringify(warnings)}`,
    );
  } finally {
    mock.timers.reset();
    console.warn = origWarn;
    filesCap.requestDownloadUrl = origRequestDownloadUrl;
    reset();
  }
});

// Unlike the "capped after ~30s" test above (the WAIT for a predecessor is capped, the
// predecessor's own promise never settles at all), this one is about the load's OWN window: it
// settles, just resolves/rejects its window-ending race with the timeout rather than with its real
// (hung) load.
test("a hung load ends its own window after ~30s and rejects with the timeout (fake timers)", async () => {
  reset();
  const filesCap = capabilities.files as unknown as { requestDownloadUrl: (...a: unknown[]) => Promise<unknown> };
  const origRequestDownloadUrl = filesCap.requestDownloadUrl;
  filesCap.requestDownloadUrl = () => new Promise(() => {});
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const hung = loadModel({ sourceName: "stuck.glb", bytes: { from: "storage", scope: "sc", glbKey: "k" }, placement: "replace" }).then(
      () => "resolved",
      (e) => e,
    );
    await new Promise((r) => setImmediate(r));
    mock.timers.tick(30_000);
    await new Promise((r) => setImmediate(r));
    const outcome = await hung;
    assert.ok(outcome instanceof Error, `the load's own window times out and rejects, got: ${outcome}`);
    assert.equal((outcome as Error).name, "ReplaceTimedOut", `got: ${outcome}`);
    assert.equal(useModelState.getState().loadedSourceNames.has("stuck.glb"), false, "an abandoned load registers nothing");
  } finally {
    mock.timers.reset();
    filesCap.requestDownloadUrl = origRequestDownloadUrl;
    reset();
  }
});

// The bug this fixes: before LOAD_WINDOW_MS existed, a load whose own fetch hung past its window
// kept running in the background, unaffected by the timeout -- so when it DID eventually settle
// (here, forced by hand well after the window closed and after a second, newer load had already
// registered), it still ran setLoadedSourceName/registerLoadedSource and clobbered the newer
// load's state. With the window and its abandoned ticket, the late settlement must register
// nothing: the registry is left exactly as the second load alone made it.
test("a late presign resolved after good.glb registered leaves exactly {good.glb}", async () => {
  reset();
  const filesCap = capabilities.files as unknown as { requestDownloadUrl: (...a: unknown[]) => Promise<unknown> };
  const origRequestDownloadUrl = filesCap.requestDownloadUrl;
  let settlePresign: ((e: unknown) => void) | undefined;
  filesCap.requestDownloadUrl = () =>
    new Promise((_resolve, reject) => {
      settlePresign = reject;
    });
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const stale = loadModel({ sourceName: "stale.glb", bytes: { from: "storage", scope: "sc", glbKey: "k" }, placement: "replace" }).then(
      () => "resolved",
      (e) => e,
    );
    const second = loadModel({ sourceName: "good.glb", bytes: { from: "url", url: "blob:test-good" }, placement: "replace", autoFitOverride: false });

    await new Promise((r) => setImmediate(r));
    mock.timers.tick(30_000);
    await new Promise((r) => setImmediate(r));
    await second;

    let names = useModelState.getState().loadedSourceNames;
    assert.deepEqual([...names], ["good.glb"], `good.glb registers once stale.glb's window closes, got: ${[...names]}`);

    // The real presign finally answers (a "late" network response), well after stale.glb's own
    // window gave up on it. Its ticket is already abandoned: this must change nothing.
    settlePresign?.(new Error("presign finally failed, too late to matter"));
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));

    names = useModelState.getState().loadedSourceNames;
    assert.deepEqual([...names], ["good.glb"], `the late settle must not re-register stale.glb or clobber good.glb, got: ${[...names]}`);
    assert.equal(useModelState.getState().loadedSourceName, "good.glb");

    const staleOutcome = await stale;
    assert.ok(staleOutcome instanceof Error, `stale.glb's own promise still settles with its timeout, got: ${staleOutcome}`);
  } finally {
    mock.timers.reset();
    filesCap.requestDownloadUrl = origRequestDownloadUrl;
    reset();
  }
});

test("loadModel.ts: replaceSource skips clearActiveFeaStreaming and uses unload_source_from_scene, not unload_any_source", () => {
  const loader = read("components/viewer/sceneHelpers/loadModel.ts");
  assert.match(loader, /placement\?: "replace" \| "overlay" \| "replaceSource"/);
  const loadFrom = loader.slice(loader.indexOf("async function loadFrom("), loader.indexOf("/** Stream a stored blob in"));
  // The native "isReplaceSource" branch shows the model without ending FEA streaming.
  const nativeReplaceSourceBranch = loadFrom.slice(
    loadFrom.indexOf("} else if (isReplaceSource) {"),
    loadFrom.indexOf("} else {", loadFrom.indexOf("} else if (isReplaceSource) {")),
  );
  assert.match(nativeReplaceSourceBranch, /unloadSameSource\(sourceName\)/);
  assert.doesNotMatch(nativeReplaceSourceBranch, /clearActiveFeaStreaming/);
});

test("jobsSlice: viewResult passes replaceSource for its own result name, replace for an explicit one; compileInBrowser passes replaceSource", () => {
  const jobsSlice = read("state/cellbuilder/jobsSlice.ts");
  assert.match(jobsSlice, /explicitSourceName \? "replace" : "replaceSource"/);
  assert.match(jobsSlice, /load_glb_from_bytes\(bytes, sourceName, false, "replaceSource"\)/);
});

// A late settle (past LOAD_WINDOW_MS) used to leave its group IN the scene: disposeObject3D frees
// GPU resources but never touches the scene graph (utils/scene/dispose_object.ts has no
// `removeFromParent` of its own), and setupModelLoaderAsync/replace_model had already run
// `main_scene.add(modelGroup)` before loadAndRegister's post-load ticket check ever ran. three.js
// re-uploads a disposed-but-still-parented object's buffers on the next render, so the stale model
// kept drawing beside the newer one. A real end-to-end repro would need a genuine GLTFLoader +
// model-worker round trip (the model-worker Comlink RPC hangs against the inert `?worker` stub
// `fixtures/panelRealm` installs for `node --test`), so this pins the fix at the source instead:
// the group leaves the scene graph BEFORE its GPU resources are freed, not just the latter alone.
test("loadModel.ts: an abandoned load's group is removed from its parent before it is disposed", () => {
  const loader = read("components/viewer/sceneHelpers/loadModel.ts");
  const loadAndRegister = loader.slice(loader.indexOf("async function loadAndRegister("));
  const start = loadAndRegister.indexOf("if (ticket?.abandoned) {");
  const abandonedCheck = loadAndRegister.slice(start, loadAndRegister.indexOf("return abandon();", start) + "return abandon();".length);
  assert.match(abandonedCheck, /group\?\.removeFromParent\(\);/);
  assert.match(abandonedCheck, /disposeObject3D\(group\);/);
  assert.ok(
    abandonedCheck.indexOf("removeFromParent") < abandonedCheck.indexOf("disposeObject3D"),
    "removeFromParent must run BEFORE disposeObject3D: dispose alone never detaches it",
  );
});

// The three.js-free viewport has no scene graph to remove a late group from -- the swap into
// `useNativeViewport`'s state IS the point that must not happen once the ticket has timed out, so
// the guard has to sit between the fetch resolving and that swap, inside showInNativeViewport
// itself (nativeModel.ts), not only in loadModel.ts's own post-load check.
test("loadModel.ts / nativeModel.ts: the native-viewport path checks the ticket again right before it swaps the model in", () => {
  const loader = read("components/viewer/sceneHelpers/loadModel.ts");
  assert.match(loader, /showInNativeViewport\(\s*[\s\S]{0,400}?\(\)\s*=>\s*ticket\?\.abandoned === true,?\s*\)/, "the show() closure threads the ticket through as the last argument");

  const native = read("viewport/native/nativeModel.ts");
  const body = native.slice(native.indexOf("export async function showInNativeViewport("));
  const fetchAt = body.indexOf("await fetch(");
  const checkAt = body.indexOf("isAbandoned?.()");
  const swapAt = body.indexOf("useNativeViewport.setState(");
  assert.ok(fetchAt >= 0 && checkAt > fetchAt, "checked again after the fetch, not only on entry");
  assert.ok(checkAt > 0 && checkAt < swapAt, "checked BEFORE the swap: a late fetch must not reach setState at all");
});
