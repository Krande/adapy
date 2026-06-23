// state/sceneHelpers/asyncModelLoader.ts
import {GLTF, GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {useConversionStore} from "@/state/conversionStore";

// All model loads share one row in the unified in-progress toast so the user gets
// download (then processing) feedback instead of a seemingly-stuck viewer on large
// models. Keyed constant => one row; the model name is shown as the row label.
const LOAD_KEY = "__model_load__";

// returns a Promise that resolves with the loaded GLTF, while reporting download
// progress into the conversion store (rendered by the unified ConversionProgress toast).
export function loadGLTF(
  modelUrl: string,
  label?: string,
  // When the model is loaded straight from an authed REST endpoint (the streaming
  // /blobs/{key} GET, which forwards Content-Encoding: gzip so the browser decompresses
  // natively), the loader's FileLoader needs the bearer header. Omitted for blob: URLs
  // and presigned S3 URLs (already signed).
  requestHeaders?: Record<string, string>,
): Promise<GLTF> {
  const loader = new GLTFLoader();
  if (requestHeaders && Object.keys(requestHeaders).length > 0) {
    loader.setRequestHeader(requestHeaders);
  }
  const name = label || modelUrl.split("/").pop()?.split("?")[0] || "model";
  const startedAt = Date.now();
  const set = (status: "running" | "error", progress: number, stage: string, error: string | null = null) =>
    useConversionStore.getState().setJob(LOAD_KEY, {
      sourceKey: LOAD_KEY,
      jobId: "load",
      derivedKey: name,
      status,
      progress,
      stage,
      error,
      startedAt,
    });

  set("running", 0, `Loading ${name}`);

  return new Promise((resolve, reject) => {
    loader.load(
      modelUrl,
      (gltf) => {
        useConversionStore.getState().clearJob(LOAD_KEY);
        resolve(gltf);
      },
      (evt) => {
        const computable = evt.lengthComputable && evt.total > 0;
        const frac = computable ? evt.loaded / evt.total : 0;
        const mb = (evt.loaded / 1e6).toFixed(0);
        // Progress events arrive during download; once it reaches 100% the loader is
        // parsing the GLB (no further events), so surface a "Processing" stage there.
        const stage =
          frac >= 1
            ? `Processing ${name}…`
            : computable
              ? `Downloading ${name}`
              : `Downloading ${name} (${mb} MB)`;
        set("running", frac, stage);
      },
      (err) => {
        set("error", 0, "Load failed", String(err));
        reject(err);
      },
    );
  });
}
