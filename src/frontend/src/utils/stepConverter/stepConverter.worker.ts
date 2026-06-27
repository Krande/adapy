// Web Worker: convert a STEP file to GLB entirely in the browser via the OCC-free adacpp wasm module
// (no pyodide, no server). Runs in a dedicated Worker so it can use OPFS synchronous access handles.
//
// The wasm module (adacpp_step_glb.js + .wasm) is served from /wasm and loaded at runtime. It exposes
// (embind):
//   stepToGlb(inPath, outPath, spillDir, deflection, angular, meshopt) -> tri count (-1 on error)
//   mountOpfs(path) -> 0 on success
// We prefer OPFS so the STEP streams through pread (bounded RSS, multi-GB files never hit the wasm
// heap); if OPFS is unavailable we fall back to the in-heap WASMFS default (fine for small/medium).

import * as Comlink from "comlink";

const WASM_URL = "/wasm/adacpp_step_glb.js";

let modulePromise: Promise<EmModule> | null = null;

interface EmModule {
  FS: {
    mkdir(path: string): void;
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    unlink(path: string): void;
  };
  mountOpfs?: (path: string) => number;
  stepToGlb: (
    inPath: string,
    outPath: string,
    spillDir: string,
    deflection: number,
    angular: number,
    meshopt: boolean,
  ) => number;
}

async function getModule(): Promise<EmModule> {
  if (!modulePromise) {
    modulePromise = (async () => {
      const mod = await import(/* @vite-ignore */ WASM_URL);
      const create = (mod.default ?? mod) as () => Promise<EmModule>;
      return await create();
    })();
  }
  return modulePromise;
}

export interface StepConvertOptions {
  deflection?: number; // libtess2 chordal tolerance (model units); default 2.0 (adapy production)
  angularDeg?: number; // default 20.0
  meshopt?: boolean; // EXT_meshopt_compression; default false
}

export interface StepConvertResult {
  glb: ArrayBuffer;
  tris: number;
  backend: "opfs" | "memfs";
  ms: number;
}

const api = {
  async convert(stepBytes: ArrayBuffer, opts: StepConvertOptions = {}): Promise<StepConvertResult> {
    const Module = await getModule();
    const FS = Module.FS;
    const t0 = performance.now();

    // Prefer OPFS (bounded RSS); fall back to the in-heap WASMFS default.
    let dir = "/work";
    let backend: "opfs" | "memfs" = "memfs";
    try {
      if (typeof Module.mountOpfs === "function" && Module.mountOpfs("/opfs") === 0) {
        dir = "/opfs";
        backend = "opfs";
      }
    } catch {
      /* OPFS unavailable -> memfs */
    }
    try {
      FS.mkdir(dir);
    } catch {
      /* already exists (mountOpfs created /opfs, or a prior run made /work) */
    }

    const inPath = `${dir}/in.step`;
    const outPath = `${dir}/out.glb`;
    FS.writeFile(inPath, new Uint8Array(stepBytes));

    const tris = Module.stepToGlb(
      inPath,
      outPath,
      dir,
      opts.deflection ?? 2.0,
      opts.angularDeg ?? 20.0,
      opts.meshopt ?? false,
    );
    if (tris < 0) {
      try {
        FS.unlink(inPath);
      } catch {
        /* ignore */
      }
      throw new Error("adacpp stepToGlb failed (unreadable STEP or write error)");
    }

    const glbU8 = FS.readFile(outPath); // view into the wasm heap
    const glb = glbU8.slice().buffer; // own, transferable copy
    for (const p of [inPath, outPath]) {
      try {
        FS.unlink(p);
      } catch {
        /* ignore */
      }
    }
    return Comlink.transfer({ glb, tris, backend, ms: performance.now() - t0 }, [glb]);
  },
};

export type StepConverterAPI = typeof api;
Comlink.expose(api);
