// The "local" CaseEngine: load combinations materialised in this browser.
//
// ``makeLocalCaseEngine`` decides per case (feaEngineChoice.ts) and either
// hands the case to the FEA engine worker -- the adacpp_fea kernel over base
// strides cached in OPFS -- or to the server engine. ANY failure of the
// browser path (module missing, OPFS error, a recipe the plan refuses, a
// worker crash) falls back to the server, so the browser engine can only ever
// make a case faster, never unavailable.
//
// A case computed here is served to the renderer through a local fetcher
// route (feaFetcher.ts): its ``cases/<n>-<hash8>/`` paths read from the store,
// so the overlay is the same shape, the same relative URLs, and nothing above
// the resolver knows where it came from. With ``upload`` the case is also
// POSTed to the server's case cache (scope write access), so the next viewer
// gets it as a plain 200.
//
// Dependencies are injected (tests drive fakes); ``defaultLocalEngineDeps``
// wires the real worker, settings and network.

import type {FeaCaseOverlay, FeaCombinationStep, FeaEnvelope, FeaManifest} from "../api/fea";
import type {CaseEngine, CaseRequest, CaseStatusInfo} from "./feaCaseResolver";
import {CASE_OVERLAY_NAME, caseDirName, planCase} from "./feaCasePlan";
import {
    chooseEngineMode,
    FEA_COMPUTE_OVERRIDE_KEY,
    FEA_COMPUTE_SETTING,
    feaSourceId,
    parseFeaComputeSetting,
    withOverride,
    type FeaComputePolicy,
    type FeaEngineCapabilities,
    type FeaEngineDecision,
    type FeaEngineEnv,
} from "./feaEngineChoice";
import type {FeaEngineClient} from "./feaEngineClient";
import {defaultStorePath, registerLocalFeaRoute, type FeaRangeFetcher, type FeaStoreReader} from "./feaFetcher";

export interface LocalEngineDeps {
    /** The worker client, or null when no worker can be made. */
    client(): Promise<FeaEngineClient | null>;
    policy(scope: string): Promise<FeaComputePolicy>;
    env(): Promise<FeaEngineEnv>;
    /** Base strides from the server (never through the local routes). */
    serverRangeFetcher(scope: string, sourceKey: string): FeaRangeFetcher;
    server: CaseEngine;
    /** POST one case file to the server's case cache. */
    upload?(scope: string, sourceKey: string, name: string, bytes: ArrayBuffer): Promise<void>;
    warn?(message: string, err?: unknown): void;
}

export interface LocalEngine extends CaseEngine {
    /** Where ``step`` (null: an envelope) would be materialised, and why. */
    decide(scope: string, manifest: FeaManifest, step: FeaCombinationStep | null): Promise<FeaEngineDecision>;
    /** ``field``'s envelope computed here, or null (the caller asks the server). */
    envelope(req: {scope: string; sourceKey: string; manifest: FeaManifest; field: string}): Promise<FeaEnvelope | null>;
    /** The last decision per case (diagnostics, the status line). */
    lastDecision(sourceKey: string, caseN: number): FeaEngineDecision | undefined;
}

async function capabilities(client: FeaEngineClient | null): Promise<FeaEngineCapabilities> {
    const worker = typeof Worker !== "undefined" && client !== null;
    if (!client) return {worker, module: false, syncAccessHandle: false, simd: false};
    const p = await client.probe();
    return {worker, module: p.ok, syncAccessHandle: p.syncAccessHandle && p.opfs, simd: p.simd};
}

export function makeLocalCaseEngine(deps: LocalEngineDeps): LocalEngine {
    const decisions = new Map<string, FeaEngineDecision>();
    const routes = new Map<string, () => void>();
    const warn = deps.warn ?? ((m: string, e?: unknown) => console.warn(`[fea] ${m}`, e ?? ""));

    async function decide(scope: string, manifest: FeaManifest, step: FeaCombinationStep | null) {
        try {
            const policy = await deps.policy(scope);
            if (policy.mode === "server") return {mode: "server" as const, reason: "policy: server"};
            if (!manifest.lazy_cases?.client_tier_a || step?.needs_raw) {
                return chooseEngineMode({policy, caps: null, env: {}, manifest, step});
            }
            const client = await deps.client();
            const [caps, env] = await Promise.all([capabilities(client), deps.env().catch(() => ({}))]);
            const d = chooseEngineMode({policy, caps, env, manifest, step});
            if (d.mode !== "server" && step) {
                // A recipe the plan refuses (a field it cannot superpose) is the server's.
                try {
                    planCase(manifest, step);
                } catch (err) {
                    return {mode: "server" as const, reason: err instanceof Error ? err.message : String(err)};
                }
            }
            return d;
        } catch (err) {
            return {mode: "server" as const, reason: `engine choice failed: ${err instanceof Error ? err.message : String(err)}`};
        }
    }

    function reader(client: FeaEngineClient, sourceId: string, bv: number): FeaStoreReader {
        return (filename, range) => client.readFile(sourceId, bv, defaultStorePath(filename), range);
    }

    function route(scope: string, sourceKey: string, prefix: string, read: FeaStoreReader) {
        const key = `${scope}::${sourceKey}::${prefix}`;
        routes.get(key)?.();
        routes.set(key, registerLocalFeaRoute(scope, sourceKey, prefix, read));
    }

    async function upload(
        client: FeaEngineClient,
        req: CaseRequest,
        sourceId: string,
        bv: number,
        overlay: FeaCaseOverlay,
    ): Promise<void> {
        if (!deps.upload) return;
        const dir = caseDirName(overlay.case);
        const names = overlay.fields.flatMap((f) => (f.per_type ? f.per_type.map((p) => p.blob.url) : f.blob ? [f.blob.url] : []));
        // Blobs first, the overlay last: the server's cache treats a case
        // with an overlay as complete.
        for (const name of [...new Set(names), CASE_OVERLAY_NAME]) {
            const bytes = await client.readFile(sourceId, bv, `cases/${dir}/${name}`);
            if (!bytes) throw new Error(`case file ${name} vanished before upload`);
            await deps.upload(req.scope, req.sourceKey, `cases/${dir}/${name}`, bytes);
        }
    }

    const engine: LocalEngine = {
        kind: "local",
        decide,
        lastDecision: (sourceKey, caseN) => decisions.get(`${sourceKey}#${caseN}`),

        async resolve(req, onStatus: (info: CaseStatusInfo) => void) {
            const decision = await decide(req.scope, req.manifest, req.step);
            decisions.set(`${req.sourceKey}#${req.step.n}`, decision);
            if (decision.mode === "server") {
                return {...(await deps.server.resolve(req, onStatus)), engine: "server" as const};
            }
            try {
                const client = await deps.client();
                if (!client) throw new Error("no engine worker");
                onStatus({status: "computing", stage: "combining in the browser"});
                const sourceId = await feaSourceId(req.scope, req.sourceKey, req.manifest);
                const bv = Number(req.manifest.bake_version ?? 0);
                const fetch = deps.serverRangeFetcher(req.scope, req.sourceKey);
                const res = await client.materialiseCase(sourceId, req.sourceKey, req.manifest, req.step.n, fetch);
                const read = reader(client, sourceId, bv);
                route(req.scope, req.sourceKey, `cases/${caseDirName(req.step)}/`, read);
                if (decision.mode === "local") route(req.scope, req.sourceKey, "", read);
                if (res.computed) {
                    const policy = await deps.policy(req.scope).catch(() => null);
                    if (policy?.upload) {
                        void upload(client, req, sourceId, bv, res.overlay).catch((err) =>
                            warn(`upload of case ${req.step.n} failed`, err),
                        );
                    }
                }
                return {overlay: res.overlay, computed: res.computed, engine: "local" as const};
            } catch (err) {
                warn(`case ${req.step.n}: browser engine failed, asking the server`, err);
                decisions.set(`${req.sourceKey}#${req.step.n}`, {
                    mode: "server",
                    reason: `browser engine failed: ${err instanceof Error ? err.message : String(err)}`,
                });
                return {...(await deps.server.resolve(req, onStatus)), engine: "server" as const};
            }
        },

        async envelope(req) {
            const decision = await decide(req.scope, req.manifest, null);
            if (decision.mode === "server") return null;
            try {
                const client = await deps.client();
                if (!client) return null;
                const sourceId = await feaSourceId(req.scope, req.sourceKey, req.manifest);
                const doc = await client.envelope(sourceId, req.manifest, req.field, deps.serverRangeFetcher(req.scope, req.sourceKey));
                return doc?.scalar_range ? doc : null;
            } catch (err) {
                warn(`envelope of ${req.field}: browser engine failed, asking the server`, err);
                return null;
            }
        },
    };
    return engine;
}

// ── the real wiring ─────────────────────────────────────────────────────────

let policyCache: Promise<unknown> | null = null;

function readOverride(): string | null {
    try {
        return globalThis.localStorage?.getItem(FEA_COMPUTE_OVERRIDE_KEY) ?? null;
    } catch {
        return null;
    }
}

/** The worker, the ``public.fea.compute`` setting, the network. */
export async function defaultLocalEngineDeps(server: CaseEngine): Promise<LocalEngineDeps> {
    let client: FeaEngineClient | null | undefined;
    return {
        server,
        async client() {
            if (client !== undefined) return client;
            try {
                if (typeof Worker === "undefined") throw new Error("no Worker");
                client = (await import("./feaEngineClient")).feaEngineClient();
            } catch {
                client = null;
            }
            return client;
        },
        async policy(scope) {
            if (!policyCache) {
                policyCache = import("../api/settings")
                    .then(({settingsApi}) => settingsApi.getPublicSetting(FEA_COMPUTE_SETTING))
                    .catch(() => null);
            }
            return withOverride(parseFeaComputeSetting(await policyCache, scope), readOverride());
        },
        async env() {
            const nav = globalThis.navigator as (Navigator & {deviceMemory?: number}) | undefined;
            const est = await nav?.storage?.estimate?.().catch(() => undefined);
            return {
                ...(est?.quota !== undefined ? {quota: est.quota} : {}),
                ...(est?.usage !== undefined ? {usage: est.usage} : {}),
                ...(typeof nav?.deviceMemory === "number" ? {deviceMemory: nav.deviceMemory} : {}),
            };
        },
        serverRangeFetcher(scope, sourceKey) {
            const prefix = `_derived/${sourceKey.replace(/^\/+/, "")}.fea/`;
            return async (filename, start, end) => {
                const {viewerApi} = await import("../viewerApi");
                return viewerApi.getBlobRange(scope as never, `${prefix}${filename.replace(/^\/+/, "")}`, start, end);
            };
        },
        async upload(scope, sourceKey, name, bytes) {
            const [{authedFetch}, {runtime}] = await Promise.all([import("../api/client"), import("@/runtime/config")]);
            const url =
                `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/fea/artefact` +
                `?source=${encodeURIComponent(sourceKey)}&name=${encodeURIComponent(name)}`;
            const r = await authedFetch(url, {method: "POST", body: bytes});
            if (!r.ok) throw new Error(`POST ${name}: ${r.status}`);
        },
    };
}

/** Forget the cached ``public.fea.compute`` (an admin changed it). */
export function clearFeaComputePolicyCache(): void {
    policyCache = null;
}
