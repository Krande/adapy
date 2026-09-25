// Main-thread wrapper for the in-browser native (no-pyodide) CAD -> GLB converter (STEP + IFC). Spins
// up the cadGlbConverter Web Worker (Comlink) and exposes kind-generic entry points. Mirrors
// diffConverter.ts.

import * as Comlink from "comlink";

import CadGlbConverterWorker from "./cadGlbConverter.worker.ts?worker&inline";
import type {
    CadGlbConverterAPI,
    NativeCadGlbResult,
    NativeClashResult,
    NativeMemberScanResult,
    CadKind,
} from "./cadGlbConverter.worker";

export type {CadKind, NativeCadGlbResult, NativeClashResult, NativeMemberScanResult} from "./cadGlbConverter.worker";

let worker: Worker | null = null;
let apiRemote: Comlink.Remote<CadGlbConverterAPI> | null = null;

function ensureApi(): Comlink.Remote<CadGlbConverterAPI> {
    if (!apiRemote) {
        worker = new CadGlbConverterWorker();
        apiRemote = Comlink.wrap<CadGlbConverterAPI>(worker);
    }
    return apiRemote;
}

// adapy production tessellation defaults (see adacpp step_to_glb_single / stream_ifc_to_glb).
const DEFAULT_DEFLECTION = 2.0;
const DEFAULT_ANGULAR_DEG = 20.0;

function tessOpts(opts?: {deflection?: number; angularDeg?: number; meshopt?: boolean}) {
    return {
        deflection: opts?.deflection ?? DEFAULT_DEFLECTION,
        angularDeg: opts?.angularDeg ?? DEFAULT_ANGULAR_DEG,
        meshopt: opts?.meshopt ?? true,
    };
}

/** Convert a STEP/IFC buffer to GLB in the worker. The buffer is transferred (consumed). */
export async function nativeCadToGlb(
    kind: CadKind,
    srcBytes: ArrayBuffer,
    opts?: {deflection?: number; angularDeg?: number; meshopt?: boolean},
): Promise<NativeCadGlbResult> {
    return ensureApi().toGlb(kind, Comlink.transfer(srcBytes, [srcBytes]), tessOpts(opts));
}

/** Does this browser/worker support the OPFS-streaming tier (worker-only sync access handles + a
 * mountable OPFS backend) for the given module? */
export async function nativeCadGlbOpfsAvailable(kind: CadKind): Promise<boolean> {
    try {
        return await ensureApi().opfsAvailable(kind);
    } catch {
        return false;
    }
}

/** Convert a STEP/IFC at a (presigned, streamable) URL to GLB, streaming the source through OPFS so a
 * multi-GB source never has to fit the wasm heap. Requires nativeCadGlbOpfsAvailable(kind). */
export async function nativeCadToGlbStreaming(
    kind: CadKind,
    sourceUrl: string,
    opts?: {deflection?: number; angularDeg?: number; meshopt?: boolean},
): Promise<NativeCadGlbResult> {
    return ensureApi().toGlbStreaming(kind, sourceUrl, tessOpts(opts));
}

/** What an IFC says its MEMBERS are -- sections, axes, outlines, placements, materials -- read in
 * the browser with no server and no tessellation. Returns the scan as JSONL text
 * (`adacpp.ifc_members/1`). The buffer is transferred (consumed). */
export async function nativeIfcMemberScan(srcBytes: ArrayBuffer): Promise<NativeMemberScanResult> {
    return ensureApi().scanMembers(Comlink.transfer(srcBytes, [srcBytes]));
}

/** The same scan, streaming the source through OPFS so a plant-scale IFC never has to fit the wasm
 * heap. Requires nativeCadGlbOpfsAvailable("ifc"). */
export async function nativeIfcMemberScanStreaming(sourceUrl: string): Promise<NativeMemberScanResult> {
    return ensureApi().scanMembersStreaming(sourceUrl);
}

/** A beam-to-beam clash check run entirely in the browser, in C++ -- the same compiled pass a
 * worker runs through adapy. Returns `adacpp.clash_joints/1` JSON. The buffer is transferred. */
export async function nativeIfcClashJoints(
    srcBytes: ArrayBuffer,
    opts?: {outOfPlaneTol?: number; pointTol?: number},
): Promise<NativeClashResult> {
    return ensureApi().clashJoints(Comlink.transfer(srcBytes, [srcBytes]), {
        outOfPlaneTol: opts?.outOfPlaneTol ?? 0.1,
        pointTol: opts?.pointTol ?? 1e-5,
    });
}
