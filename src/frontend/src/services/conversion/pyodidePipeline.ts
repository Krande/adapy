// In-browser conversion pipeline using Pyodide.
// Two source formats supported, picked by extension:
//   - .ifc → ifcopenshell (wasm wheel) + trimesh
//   - .step / .stp → adacpp (wasm wheel, OCCT-cross-compiled) via adapy.cad
// Fetches source bytes from storage, runs the conversion in a Web
// Worker, then PUTs the resulting GLB back to storage so the existing
// VIEW_FILE_OBJECT path can serve it.

import {useConversionStore, ConversionJob} from "@/state/conversionStore";
import {convertViaPyodide, type PyodideSourceFormat} from "@/utils/pyodide/pyodide_converter";
import {viewerApi, ScopeUrl} from "@/services/viewerApi";

function detectFormat(sourceKey: string): PyodideSourceFormat {
    const lower = sourceKey.toLowerCase();
    if (lower.endsWith(".ifc")) return "ifc";
    if (lower.endsWith(".step") || lower.endsWith(".stp")) return "step";
    throw new Error(
        `pyodide pipeline does not support source extension for ${sourceKey} ` +
            `(expected .ifc / .step / .stp)`,
    );
}

export async function convertViaPyodideAndUpload(
    scope: ScopeUrl,
    sourceKey: string,
): Promise<string> {
    const format = detectFormat(sourceKey);
    const storeKey = `${sourceKey}::glb`;
    const store = useConversionStore.getState();
    const job: ConversionJob = {
        sourceKey: storeKey,
        jobId: "pyodide",
        derivedKey: "",
        status: "running",
        progress: 0.05,
        stage: "fetching source",
        error: null,
        startedAt: Date.now(),
    };
    store.setJob(storeKey, job);

    const sourceBuf = await viewerApi.getBlob(scope, sourceKey);

    store.setJob(storeKey, {...job, progress: 0.15, stage: `tessellating ${format} in browser`});

    const glb = await convertViaPyodide(format, sourceBuf, {
        onLog: (msg) => store.setJob(storeKey, {
            ...store.jobs[storeKey] || job,
            stage: msg,
        }),
    });

    store.setJob(storeKey, {
        ...store.jobs[storeKey] || job,
        progress: 0.9,
        stage: "uploading derived",
    });

    // putBlob to ``_derived/*`` is rejected by the API (that namespace
    // is server-worker territory). The pyodide-derived blob goes via a
    // dedicated route that takes (source, target) and computes the
    // canonical derived key server-side, matching whatever the worker
    // would have written.
    const derivedKey = await viewerApi.putDerivedBlob(scope, sourceKey, "glb", glb);

    store.setJob(storeKey, {
        ...store.jobs[storeKey] || job,
        progress: 1.0,
        stage: "ready",
        status: "done",
        derivedKey,
    });
    return derivedKey;
}
