// Main-thread handle on the FEA engine worker (workers/feaEngine.worker.ts).
//
// One worker per page, created on first use. A worker that cannot start, or a
// module that cannot load (not deployed, no SIMD, ...), makes ``probe`` say
// so and the engine choice keeps every case on the server.
//
// Imported lazily (``await import("./feaEngineClient")``): the ``?worker``
// import is a bundler construct, and the pure modules around it are run under
// plain node by the tests.

import * as Comlink from "comlink";

import type {FeaEnvelope, FeaManifest} from "../api/fea";
import FeaEngineWorker from "../../workers/feaEngine.worker.ts?worker&inline";
import type {FeaEngineProbe, FeaEngineWorkerAPI} from "../../workers/feaEngine.worker";
import type {MaterialisedCase} from "./feaEngineCore";
import type {FeaRangeFetcher} from "./feaFetcher";
import type {FeaStoreUsage} from "./opfsFeaStore";

export type {FeaEngineProbe} from "../../workers/feaEngine.worker";

/** What the rest of the viewer calls; every method may reject. */
export interface FeaEngineClient {
    probe(): Promise<FeaEngineProbe>;
    materialiseCase(
        sourceId: string,
        sourceKey: string,
        manifest: FeaManifest,
        caseN: number,
        fetch: FeaRangeFetcher,
    ): Promise<MaterialisedCase>;
    envelope(sourceId: string, manifest: FeaManifest, field: string, fetch: FeaRangeFetcher): Promise<FeaEnvelope>;
    readFile(sourceId: string, bakeVersion: number, rel: string, range?: {start: number; end: number}): Promise<ArrayBuffer | null>;
    usage(): Promise<FeaStoreUsage>;
    evict(targetBytes?: number): Promise<string[]>;
    clear(sourceId?: string): Promise<void>;
}

let remote: Comlink.Remote<FeaEngineWorkerAPI> | null = null;
let probePromise: Promise<FeaEngineProbe> | null = null;

function api(): Comlink.Remote<FeaEngineWorkerAPI> {
    if (!remote) remote = Comlink.wrap<FeaEngineWorkerAPI>(new FeaEngineWorker());
    return remote;
}

export function feaEngineClient(): FeaEngineClient {
    return {
        probe() {
            if (!probePromise) {
                probePromise = (async () => {
                    try {
                        return await api().probe();
                    } catch (err) {
                        return {
                            ok: false,
                            error: err instanceof Error ? err.message : String(err),
                            opfs: false,
                            syncAccessHandle: false,
                            simd: false,
                        };
                    }
                })();
            }
            return probePromise;
        },
        materialiseCase: (sourceId, sourceKey, manifest, caseN, fetch) =>
            api().materialiseCase(sourceId, sourceKey, manifest, caseN, Comlink.proxy(fetch)),
        envelope: (sourceId, manifest, field, fetch) => api().envelope(sourceId, manifest, field, Comlink.proxy(fetch)),
        readFile: (sourceId, bakeVersion, rel, range) => api().readFile(sourceId, bakeVersion, rel, range),
        usage: () => api().usage(),
        evict: (targetBytes) => api().evict(targetBytes),
        clear: (sourceId) => api().clear(sourceId),
    };
}
