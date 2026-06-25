// state/sceneHelpers/asyncModelLoader.ts
import {GLTF, GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
// Use the meshoptimizer npm decoder (1.1.x), NOT three's bundled meshopt_decoder
// (built from meshoptimizer 0.18, vertex-codec v0 only). adacpp's encoder
// (vendored meshoptimizer 1.1) emits the v1 vertex codec by default, which the
// 0.18 decoder rejects with "malformed buffer". The 1.1 decoder reads both v0/v1.
import {MeshoptDecoder} from "meshoptimizer";
import {useConversionStore} from "@/state/conversionStore";
import type {LoadMetricsRecorder} from "@/utils/scene/loadMetrics";

// All model loads share one row in the unified in-progress toast so the user gets
// download (then processing) feedback instead of a seemingly-stuck viewer on large
// models. Keyed constant => one row; the model name is shown as the row label.
const LOAD_KEY = "model-load";

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
  // Optional admin load-metrics recorder — receives download/parse phase
  // marks so the network vs CPU split can be measured. No-op when absent.
  metrics?: LoadMetricsRecorder | null,
): Promise<GLTF> {
  const loader = new GLTFLoader();
  // EXT_meshopt_compression support. Harmless for uncompressed GLBs (the
  // decoder is only invoked when the file declares the extension), so it's
  // always registered — the backend "glb_compression" toggle decides
  // whether a given GLB uses it. KHR_mesh_quantization (the upload/VRAM
  // win) needs no decoder — three.js core dequantizes on read.
  loader.setMeshoptDecoder(MeshoptDecoder);
  if (requestHeaders && Object.keys(requestHeaders).length > 0) {
    loader.setRequestHeader(requestHeaders);
  }
  metrics?.setUrl(modelUrl);
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
        // Download finished + GLB parsed by now. (markDownloadDone may
        // already have fired at the 100%-progress edge below.)
        metrics?.markDownloadDone();
        metrics?.markParseDone();
        useConversionStore.getState().clearJob(LOAD_KEY);
        resolve(gltf);
      },
      (evt) => {
        const computable = evt.lengthComputable && evt.total > 0;
        const frac = computable ? evt.loaded / evt.total : 0;
        const mb = (evt.loaded / 1e6).toFixed(0);
        // Progress events arrive during download; once it reaches 100% the loader is
        // parsing the GLB (no further events), so surface a "Processing" stage there.
        if (frac >= 1) metrics?.markDownloadDone();
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
