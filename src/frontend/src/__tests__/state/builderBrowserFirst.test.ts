// The builder compiles in the browser by default, LOCAL ONLY; committing stores the model.
//
// A real build needs Pyodide, which `node --test` does not run, so this pins the routing at the
// source (as replaceSource.test.ts does for the loaders):
//  - Compile (committed) and Compile preview build the simulation LOD in the browser first, and
//    reach the server worker only when the execution policy's "procedural_compile" order says so (worker first,
//    or after a failed browser build) or for a server-only engine;
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

test("the policy order decides; detail and server-only engines stay unjudged worker jobs", () => {
  const start = jobs.indexOf("const routeBuild = async");
  assert.ok(start >= 0);
  const b = jobs.slice(start, jobs.indexOf("  return {", start));
  assert.match(b, /if \(lod === "detail"\) return \{ runOnWorker: false \};/);
  assert.match(b, /planExecution\("procedural_compile", get\(\)\.selectedEngine \|\| "\*"/);
  assert.match(b, /if \(engine === "worker"\) return \{ runOnWorker: plan\.browserPath \};/);
  assert.match(b, /await runBrowserCompile\(label\)/);
  assert.match(b, /if \(outcome === "server-only"\) return \{ runOnWorker: false \};/);
});

test("the browser build is local only", () => {
  const start = jobs.indexOf("const runBrowserCompile = async");
  const end = jobs.indexOf("const routeBuild", start);
  const b = jobs.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.match(b, /compileProceduralViaPyodide\(/);
  assert.match(b, /load_glb_from_bytes\(bytes, sourceName, false, "replaceSource"\)/);
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
  // A worker-advertised engine is handed over without asking the server to resolve it.
  assert.match(b, /eng\.origin === "worker"\)[\s\S]*?return toServer\(\);/);
});

test("committing stores the model on the server", () => {
  const doc = read("state/cellbuilder/documentSlice.ts");
  const commit = doc.slice(doc.indexOf("commit: async () => {"));
  assert.match(commit, /capabilities\.procedural\.commitModel\(/);
});
