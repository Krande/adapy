// The builder compiles in the browser by default, LOCAL ONLY; committing stores the model.
//
// A real build needs Pyodide, which `node --test` does not run, so this pins the routing at the
// source (as replaceSource.test.ts does for the loaders):
//  - Compile (committed) and Compile preview build in the browser first (creating a model from
//    a template builds nothing until asked), and reach the server worker only when the execution policy's "procedural_compile"
//    order says so (worker first, or after a failed browser build) or when the build has no
//    browser path (services/compute/proceduralBrowserEngine, tested on its own);
//  - the browser build uploads nothing: no server compile/preview call, no blob write;
//  - commit still saves the document on the server (commitModel), not a worker job.
import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const read = (rel: string): string => readFileSync(new URL(`../../${rel}`, import.meta.url), "utf8");

const jobs = read("state/cellbuilder/jobsSlice.ts");

function body(name: string): string {
  const start = jobs.indexOf(`    ${name}: async (`);
  assert.ok(start >= 0, `${name} not found`);
  const next = jobs.indexOf("\n    }", start);
  return jobs.slice(start, next);
}

test("compile and compile preview route through the execution policy before the worker", () => {
  for (const name of ["compile", "compilePreview"]) {
    const b = body(name);
    const routeAt = b.indexOf("await routeBuild(lod, ");
    const serverAt = b.indexOf("startCompileJob(");
    assert.ok(routeAt >= 0 && serverAt > routeAt, `${name}: routed first, worker after`);
    assert.match(b, /if \(route === "done" \|\| route === null\) return;/);
    assert.match(b, /runOnWorker: route\.runOnWorker/);
  }
});

test("the policy order decides; server-only builds stay unjudged worker jobs", () => {
  const start = jobs.indexOf("const routeBuild = async");
  assert.ok(start >= 0);
  const b = jobs.slice(start, jobs.indexOf("  return {", start));
  // The detail LOD asks browserEngineRoute first; with no browser path it is an unjudged worker job.
  assert.match(
    b,
    /if \(lod === "detail"\) \{[\s\S]*?browserEngineRoute\(\{[\s\S]*?lod,[\s\S]*?if \(route\.kind === "server-only"\) return \{ runOnWorker: false \};/,
  );
  assert.ok(b.indexOf("browserEngineRoute(") < b.indexOf("refreshAccessIfStale()"), "detail is judged before the policy");
  assert.match(b, /planExecution\("procedural_compile", get\(\)\.selectedEngine \|\| "\*"/);
  assert.match(b, /if \(engine === "worker"\) return \{ runOnWorker: plan\.browserPath \};/);
  assert.match(b, /await runBrowserCompile\(label, lod\)/);
  assert.match(b, /if \(outcome === "server-only"\) return \{ runOnWorker: false \};/);
});

test("the browser build is local only", () => {
  const start = jobs.indexOf("const runBrowserCompile = async");
  const end = jobs.indexOf("const routeBuild", start);
  const b = jobs.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.match(b, /compileProceduralViaPyodide\(/);
  // An engine a baked plugin wheel registers runs through that plugin's install plan.
  assert.match(b, /route\.kind === "baked"[\s\S]*?runProceduralEngineViaPyodide\(route\.engine, doc, \{ onLog, lod \}\)/);
  assert.match(b, /load_glb_from_bytes\(bytes, sourceName, false, "replaceSource"\)/);
  // Each LOD loads under its own source, as the server results do (viewResult).
  assert.match(b, /lod === "detail" \? `procedural-detail:\$\{active\.name\}` : `procedural:\$\{active\.name\}`/);
  for (const server of ["previewModel", "compileModel", "putBlob", "uploadDerived", "fetch("]) {
    assert.ok(!b.includes(server), `the browser build must not reach the server (${server})`);
  }
});

test("handing a build to the server removes the browser's toast", () => {
  // Every "server-only" return goes through toServer(), which clears the toast the
  // browser attempt put up; left "running", it never finishes and the build looks stuck.
  const start = jobs.indexOf("const runBrowserCompile = async");
  const end = jobs.indexOf("const routeBuild", start);
  const b = jobs.slice(start, end);
  assert.match(b, /const toServer = \(\): "server-only" => \{[\s\S]*?clearJob\(proceduralToastKey\(label\)\)/);
  const bare = b.replace(/const toServer = [\s\S]*?\n {4}\};/, "").match(/return "server-only"/g) ?? [];
  assert.equal(bare.length, 0, "a server-only return bypasses toServer()");
  // An engine with no browser path (browserEngineRoute: a worker-advertised engine no baked
  // wheel registers, a detail build) is handed over without asking the server to resolve it.
  assert.match(b, /if \(route\.kind === "server-only"\) return toServer\(\);/);
  assert.ok(
    b.indexOf('route.kind === "server-only"') < b.indexOf("resolveEngine("),
    "server-only is decided before the resolve call",
  );
});

test("creating a model from a template opens it and starts no build", () => {
  // A build the user did not ask for lands whenever it finishes (a first browser build installs
  // the engine first), in the middle of whatever the user does with the model by then, and it
  // stores nothing. The model builds when asked: Compile, or opening a result view.
  const src = read("components/storage/browser/useProceduralModels.ts");
  const start = src.indexOf("const createProceduralModelFromTemplate = async");
  assert.ok(start >= 0);
  const b = src.slice(start, src.indexOf("\n    };", start));
  // Created for the template's own engine (not the deployment's default), its doc naming it.
  assert.match(b, /createProceduralModel\(scopeKey, name\.trim\(\), \{engine: tpl\.engine\}\)/);
  assert.match(b, /const doc = tpl\.doc\.engine \? tpl\.doc : \{\.\.\.tpl\.doc, engine: tpl\.engine\}/);
  assert.match(b, /commitProceduralModel\(scopeKey, detail\.id, doc, detail\.revision\)/);
  assert.match(b, /\.open\(fresh\.id, fresh\.name, revision, fresh\.doc\)/);
  for (const build of ["compileProceduralModel(", "previewProceduralModel(", ".compile(", ".compilePreview(", "compileInBrowser("]) {
    assert.ok(!b.includes(build), `template create must not start a build (${build})`);
  }
});

test("committing stores the model on the server", () => {
  const doc = read("state/cellbuilder/documentSlice.ts");
  const commit = doc.slice(doc.indexOf("commit: async () => {"));
  assert.match(commit, /capabilities\.procedural\.commitModel\(/);
});
