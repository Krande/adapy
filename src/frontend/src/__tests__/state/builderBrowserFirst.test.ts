// The builder compiles in the browser by default, LOCAL ONLY; committing stores the model.
//
// A real build needs Pyodide, which `node --test` does not run, so this pins the routing at the
// source (as replaceSource.test.ts does for the loaders):
//  - Compile (committed) and Compile preview build the simulation LOD in the browser first, and
//    reach the server worker only on the user's opt-in (workerPreferred) or for a server-only engine;
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

test("compile and compile preview try the browser before the worker", () => {
  for (const name of ["compile", "compilePreview"]) {
    const b = body(name);
    const browserAt = b.indexOf("runBrowserCompile(");
    const serverAt = b.indexOf("startCompileJob(");
    assert.ok(browserAt >= 0 && serverAt > browserAt, `${name}: browser first, worker after`);
    assert.match(b, /buildsInBrowser\(lod\) && \(await runBrowserCompile\(.*?\)\) !== "server-only"\) return;/);
  }
});

test("only the opt-in moves a simulation build to the worker; detail stays a worker job", () => {
  assert.match(jobs, /const buildsInBrowser = \(lod: "sim" \| "detail"\) => lod === "sim" && !workerPreferred\(\);/);
});

test("the browser build is local only", () => {
  const start = jobs.indexOf("const runBrowserCompile = async");
  const end = jobs.indexOf("const buildsInBrowser", start);
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
  const end = jobs.indexOf("const buildsInBrowser", start);
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
