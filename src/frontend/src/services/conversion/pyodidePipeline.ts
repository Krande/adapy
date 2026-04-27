// In-browser conversion pipeline using Pyodide + ifcopenshell WASM.
// Fetches source bytes from storage, runs the conversion in a Web
// Worker, then PUTs the resulting GLB back to storage so the existing
// VIEW_FILE_OBJECT path can serve it.

import {useConversionStore, ConversionJob} from "@/state/conversionStore";
import {convertIfcViaPyodide} from "@/utils/pyodide/pyodide_converter";
import {viewerApi} from "@/services/viewerApi";

export async function convertViaPyodideAndUpload(sourceKey: string): Promise<string> {
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

    const sourceBuf = await viewerApi.getBlob(sourceKey);

    store.setJob(storeKey, {...job, progress: 0.15, stage: "tessellating in browser"});

    const glb = await convertIfcViaPyodide(sourceBuf, {
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

    const derivedKey = `_derived/${sourceKey}.glb`;
    await viewerApi.putBlob(derivedKey, glb);

    store.setJob(storeKey, {
        ...store.jobs[storeKey] || job,
        progress: 1.0,
        stage: "ready",
        status: "done",
        derivedKey,
    });
    return derivedKey;
}
