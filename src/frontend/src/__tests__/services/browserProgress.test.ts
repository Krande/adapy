// An in-browser conversion's progress (services/conversion/browserProgress): the worker's boot
// and install lines move the setup band, adapy's (stage, fraction) reports the convert band, the
// bar never goes backwards and the store is written at most once per interval. And the wiring:
// the Pyodide worker hands wasm_convert a progress callback and posts what it is told.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";

import {
  BROWSER_BANDS,
  BROWSER_HINT,
  browserJobProgress,
  createBrowserProgress,
  type ProgressPatch,
} from "@/services/conversion/browserProgress";
import { useConversionStore, type ConversionJob } from "@/state/conversionStore";

// A fake clock and timer queue, so the throttle is tested without waiting.
function fakeTime() {
  let t = 0;
  let next = 1;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
    now: () => t,
    setTimer: (fn: () => void, ms: number) => {
      const id = next++;
      timers.set(id, { at: t + ms, fn });
      return id;
    },
    clearTimer: (id: unknown) => void timers.delete(id as number),
    advance(ms: number) {
      t += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= t) {
          timers.delete(id);
          timer.fn();
        }
      }
    },
    pending: () => timers.size,
  };
}

function recorder() {
  const writes: ProgressPatch[] = [];
  const time = fakeTime();
  const progress = createBrowserProgress((p) => writes.push(p), {
    now: time.now,
    setTimer: time.setTimer,
    clearTimer: time.clearTimer,
  });
  return { writes, time, progress };
}

const fractions = (writes: ProgressPatch[]) => writes.map((w) => w.progress);
const isSorted = (xs: number[]) => xs.every((x, i) => i === 0 || xs[i - 1] <= x);

describe("browser conversion progress", () => {
  it("moves through the setup band on each distinct boot / install line, never past it", () => {
    const { writes, time, progress } = recorder();
    const lines = [
      "Loading Pyodide v0.29.4 from /pyodide/…",
      "Loading numpy + Pillow + micropip…",
      "Loading numpy + Pillow + micropip…", // a repeat is not a new stage
      "Fetching adapy (pyodide) wheel…",
      "Installing SAT stack (pydantic + Pillow)…",
      "Verifying SAT stack…",
    ];
    for (const line of lines) {
      progress.setupLog(line);
      time.advance(200);
    }
    assert.equal(writes.length, 5);
    assert.ok(isSorted(fractions(writes)));
    const [lo, hi] = BROWSER_BANDS.setup;
    for (const w of writes) assert.ok(w.progress > lo && w.progress < hi, `${w.progress} outside the setup band`);
    assert.equal(writes[0].stage, `Loading Pyodide v0.29.4 from /pyodide/…${BROWSER_HINT}`);
    assert.equal(writes.at(-1)!.stage, `Verifying SAT stack…${BROWSER_HINT}`);
  });

  it("maps adapy's fraction onto the convert band and says it runs in the browser", () => {
    const { writes, time, progress } = recorder();
    const [lo, hi] = BROWSER_BANDS.convert;
    progress.convert("reading Genie XML", 0);
    time.advance(200);
    progress.convert("tessellating (12/40 objects)", 0.5);
    time.advance(200);
    progress.convert("done", 1);
    const got = fractions(writes);
    assert.equal(got.length, 3);
    assert.equal(got[0], lo);
    assert.ok(Math.abs(got[1] - (lo + hi) / 2) < 1e-12);
    assert.equal(got[2], hi);
    assert.equal(writes[1].stage, `tessellating (12/40 objects)${BROWSER_HINT}`);
  });

  it("never goes backwards", () => {
    const { writes, time, progress } = recorder();
    progress.stage("fetching source", 0.02);
    progress.setupLog("Loading Pyodide…");
    time.advance(200);
    progress.convert("tessellating", 0.8);
    time.advance(200);
    progress.convert("a sub-step reporting less", 0.1);
    time.advance(200);
    progress.stage("uploading derived", 0.9);
    assert.ok(isSorted(fractions(writes)), JSON.stringify(writes));
    assert.equal(writes[3].stage, `a sub-step reporting less${BROWSER_HINT}`);
    assert.equal(writes[3].progress, writes[2].progress);
  });

  it("writes at most once per interval and keeps the last held-back update", () => {
    const { writes, time, progress } = recorder();
    for (let i = 0; i <= 1000; i++) {
      progress.convert(`tessellating (${i}/1000 objects)`, i / 1000);
      time.advance(0.05); // 1000 reports in 50 ms
    }
    assert.equal(writes.length, 1, "a report storm must not write the store per report");
    time.advance(100);
    assert.equal(writes.length, 2);
    assert.equal(writes[1].stage, `tessellating (1000/1000 objects)${BROWSER_HINT}`);
    assert.equal(writes[1].progress, BROWSER_BANDS.convert[1]);
    assert.equal(time.pending(), 0);
  });

  it("ignores install lines once adapy reports (a plugin prewarm's logs are not this job's)", () => {
    const { writes, time, progress } = recorder();
    progress.convert("reading IFC", 0.1);
    time.advance(200);
    progress.setupLog("Installing Python deps for some-plugin…");
    time.advance(200);
    assert.equal(writes.length, 1);
  });

  it("a pipeline stage is written at once, and dispose drops what is held back", () => {
    const { writes, time, progress } = recorder();
    progress.convert("tessellating", 0.2);
    progress.convert("tessellating", 0.3); // held back
    progress.stage("uploading derived", BROWSER_BANDS.upload);
    assert.equal(writes.at(-1)!.stage, "uploading derived");
    progress.convert("late", 0.99);
    progress.dispose();
    time.advance(1000);
    assert.equal(writes.at(-1)!.stage, "uploading derived");
    assert.equal(time.pending(), 0);
  });
});

describe("browserJobProgress", () => {
  const base: ConversionJob = {
    sourceKey: "a.xml::glb",
    jobId: "pyodide",
    derivedKey: "",
    status: "running",
    progress: 0.02,
    stage: "fetching source",
    error: null,
    startedAt: 1,
  };

  it("writes into the running job's store entry", () => {
    useConversionStore.setState({ jobs: { "a.xml::glb": base } });
    const p = browserJobProgress("a.xml::glb", base, { minIntervalMs: 0 });
    p.convert("tessellating (1/2 objects)", 0.5);
    const job = useConversionStore.getState().jobs["a.xml::glb"];
    assert.equal(job.stage, `tessellating (1/2 objects)${BROWSER_HINT}`);
    assert.ok(Math.abs(job.progress - (BROWSER_BANDS.convert[0] + BROWSER_BANDS.convert[1]) / 2) < 1e-12);
    assert.equal(job.jobId, "pyodide");
  });

  it("does not revive a dismissed or finished job", () => {
    useConversionStore.setState({ jobs: {} });
    const p = browserJobProgress("a.xml::glb", base, { minIntervalMs: 0 });
    p.convert("tessellating", 0.5);
    assert.equal(useConversionStore.getState().jobs["a.xml::glb"], undefined);
    useConversionStore.setState({ jobs: { "a.xml::glb": { ...base, status: "done", progress: 1, stage: "ready" } } });
    p.convert("tessellating", 0.6);
    assert.equal(useConversionStore.getState().jobs["a.xml::glb"].stage, "ready");
  });
});

// The worker is a classic worker no test can boot; its wiring is pinned in its source.
describe("Pyodide worker progress wiring", () => {
  const read = (rel: string) => fs.readFileSync(path.resolve(import.meta.dirname, "../..", rel), "utf8");

  it("hands wasm_convert a progress callback in every conversion entry and posts it per request", () => {
    const src = read("utils/pyodide/pyodide_worker.js");
    assert.match(src, /_wc\.run\([^)]*\*\*_wc_progress_kw\(_wc\.run, _wc_progress\)/);
    assert.equal((src.match(/_wc\.run_stream\([\s\S]*?\*\*_wc_progress_kw\(_wc\.run_stream, _wcs_progress\)/g) || []).length, 2);
    // Each conversion cell defines the keyword helper first (an older adapy wheel takes none).
    assert.equal((src.match(/runPythonAsync\(`\$\{PROGRESS_KW_PY\}/g) || []).length, 3);
    assert.match(src, /"on_progress" in inspect\.signature\(fn\)\.parameters/);
    assert.match(src, /function conversionProgress\(reqId\)[\s\S]*?type: "progress",\s*reqId/);
    for (const fn of ["runConversion", "runConversionStream", "runFeaBakeStream"]) {
      assert.match(src, new RegExp(`async function ${fn}\\(reqId,`), `${fn} is not told its request`);
    }
  });

  it("forwards a request's progress messages to the caller of a conversion", () => {
    const src = read("utils/pyodide/pyodide_converter.ts");
    assert.match(src, /data\.type === "progress"[\s\S]*?data\.reqId === reqId[\s\S]*?onProgress\?\.\(data\.message, data\.fraction\)/);
    assert.equal((src.match(/awaitWorkerResult\(worker, reqId, opts\?\.onLog, opts\?\.onProgress\)/g) || []).length, 3);
  });
});
