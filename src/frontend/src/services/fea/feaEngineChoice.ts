// Where a load combination is materialised: on the server, or in this browser.
//
// THE MODES.
//   * ``server`` -- ``GET .../fea/case`` (200 overlay, or 202 + poll). Always
//     available, and the fallback for everything below on any failure.
//   * ``hybrid`` -- the base bake stays on the server (the viewer reads it from
//     there as before); combinations are superposed in the browser by the
//     adacpp_fea worker, over base strides it fetches ONCE into OPFS. The
//     default when the browser can.
//   * ``local`` -- as hybrid, and the viewer also reads base strides the store
//     already holds from OPFS rather than the network.
//
// THE POLICY is a public setting, per scope, written by an admin like the
// other ``public.*`` scope maps:
//
//   public.fea.compute = {"<scope url>": "auto" | "server" | "client"
//                                      | {"mode": ..., "upload": true}, "*": ...}
//
// ``auto`` (the default, also for a scope nobody configured) lets this module
// pick hybrid when the browser is capable and roomy, server otherwise;
// ``server`` never computes in the browser; ``client`` computes locally
// whenever the browser CAN (whatever its memory), i.e. ``local``. ``upload``
// (scope write access) sends a browser-computed case back to the server's
// case cache, producer-stamped, so the next viewer gets it as a 200.
//
// A per-viewer override (``localStorage["adapy.fea.compute"]``, same values)
// is honoured for trying things out, within what the scope allows: a scope
// set to ``server`` stays server.
//
// Pure (no DOM access here; the caller passes what it probed), so the rules
// are unit tested.

import type {FeaCombinationStep, FeaManifest} from "../api/fea";
import {caseFields} from "./feaCasePlan";

export const FEA_COMPUTE_SETTING = "public.fea.compute";
export const FEA_COMPUTE_OVERRIDE_KEY = "adapy.fea.compute";

export type FeaComputeMode = "server" | "client" | "auto";
export type FeaEngineMode = "server" | "hybrid" | "local";

export interface FeaComputePolicy {
    mode: FeaComputeMode;
    /** Upload browser-computed cases to the server's cache. */
    upload: boolean;
}

export const DEFAULT_FEA_COMPUTE_POLICY: FeaComputePolicy = {mode: "auto", upload: false};

function asMode(v: unknown): FeaComputeMode | null {
    return v === "server" || v === "client" || v === "auto" ? v : null;
}

function asPolicy(v: unknown): FeaComputePolicy | null {
    const mode = asMode(v);
    if (mode) return {mode, upload: false};
    if (v && typeof v === "object" && !Array.isArray(v)) {
        const o = v as {mode?: unknown; upload?: unknown};
        return {mode: asMode(o.mode) ?? "auto", upload: o.upload === true};
    }
    return null;
}

/** The policy for ``scope`` from the stored setting (JSON text or object).
 *  Missing / malformed reads as the default: ``auto``, no upload. */
export function parseFeaComputeSetting(raw: unknown, scope: string): FeaComputePolicy {
    let parsed: unknown = raw;
    if (typeof raw === "string") {
        const mode = asMode(raw.trim());
        if (mode) return {mode, upload: false};
        try {
            parsed = JSON.parse(raw);
        } catch {
            return DEFAULT_FEA_COMPUTE_POLICY;
        }
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return DEFAULT_FEA_COMPUTE_POLICY;
    const map = parsed as Record<string, unknown>;
    return asPolicy(map[scope]) ?? asPolicy(map["*"]) ?? DEFAULT_FEA_COMPUTE_POLICY;
}

/** ``policy`` with a per-viewer override (a mode string) applied. A scope an
 *  admin set to ``server`` stays server: the override only chooses within
 *  what the scope allows. */
export function withOverride(policy: FeaComputePolicy, override: string | null | undefined): FeaComputePolicy {
    if (policy.mode === "server") return policy;
    const mode = asMode(override?.trim());
    return mode ? {...policy, mode} : policy;
}

/** What the browser can do (the worker reports the last three). */
export interface FeaEngineCapabilities {
    worker: boolean;
    /** The adacpp_fea module loaded in the worker. */
    module: boolean;
    /** ``FileSystemSyncAccessHandle`` in the worker (OPFS the module can use). */
    syncAccessHandle: boolean;
    simd: boolean;
}

export interface FeaEngineEnv {
    /** ``navigator.storage.estimate()``. */
    quota?: number;
    usage?: number;
    /** ``navigator.deviceMemory`` (GiB, coarse; Chromium only). */
    deviceMemory?: number;
}

export interface FeaEngineDecision {
    mode: FeaEngineMode;
    reason: string;
}

/** Below this much device memory ``auto`` leaves combinations to the server. */
export const MIN_DEVICE_MEMORY_GIB = 4;

/** Bytes a case's materialisation touches in the store: per blob of every
 *  case field, the strides of its terms plus the single-step result. */
export function estimateCaseBytes(manifest: FeaManifest, step: Pick<FeaCombinationStep, "terms"> | null): number {
    const terms = step?.terms?.length ?? 1;
    const wanted = new Set(caseFields(manifest));
    let total = 0;
    for (const f of manifest.fields ?? []) {
        if (!wanted.has(f.name_canonical)) continue;
        const strides = f.per_type?.length ? f.per_type.map((p) => p.blob.stride_bytes) : f.blob ? [f.blob.stride_bytes] : [];
        for (const s of strides) total += s * (terms + 1) + 2048;
    }
    return total;
}

/** The engine for one case (``step``; null = an envelope over every Tier-A case). */
export function chooseEngineMode(args: {
    policy: FeaComputePolicy;
    caps: FeaEngineCapabilities | null;
    env: FeaEngineEnv;
    manifest: FeaManifest;
    step: FeaCombinationStep | null;
}): FeaEngineDecision {
    const {policy, caps, env, manifest, step} = args;
    if (policy.mode === "server") return {mode: "server", reason: "policy: server"};
    if (!manifest.lazy_cases?.client_tier_a) return {mode: "server", reason: "the bake offers no client-side combinations"};
    if (step?.needs_raw) return {mode: "server", reason: step.raw_reason || "the recipe needs the raw records"};
    if (!caps) return {mode: "server", reason: "browser engine not probed"};
    if (!caps.worker) return {mode: "server", reason: "no Web Worker"};
    if (!caps.module) return {mode: "server", reason: "adacpp_fea module unavailable"};
    if (!caps.syncAccessHandle) return {mode: "server", reason: "no OPFS sync access handles"};
    if (!caps.simd) return {mode: "server", reason: "no WebAssembly SIMD"};
    if (env.quota !== undefined) {
        const free = env.quota - (env.usage ?? 0);
        const need = estimateCaseBytes(manifest, step);
        if (free < 2 * need) return {mode: "server", reason: `storage quota: ${free} B free, ${need} B needed`};
    }
    if (policy.mode === "client") return {mode: "local", reason: "policy: client"};
    if (env.deviceMemory !== undefined && env.deviceMemory < MIN_DEVICE_MEMORY_GIB) {
        return {mode: "server", reason: `device memory ${env.deviceMemory} GiB < ${MIN_DEVICE_MEMORY_GIB}`};
    }
    return {mode: "hybrid", reason: "auto: capable browser"};
}

/** The store's key for a server source: a digest of where the bake comes from
 *  and what it is, so a re-baked source (a new manifest) never reads another
 *  bake's strides. ``source_sha256`` when the bake stamps it, else the
 *  manifest itself. */
export async function feaSourceId(scope: string, sourceKey: string, manifest: FeaManifest): Promise<string> {
    const identity = (manifest as {source_sha256?: string}).source_sha256 ?? JSON.stringify(manifest);
    const text = `${scope}\n${sourceKey.replace(/^\/+/, "")}\n${manifest.bake_version ?? 0}\n${identity}`;
    const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}
