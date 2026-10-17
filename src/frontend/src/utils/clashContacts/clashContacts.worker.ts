// The convex-contact clash check on a viewer GLB, in a worker: adacpp's `adacpp_clash` wasm module
// (`findGlbContacts`), the same C++ a worker runs through `adacpp.clash.find_glb_contacts`. Bytes in,
// columns out -- see `services/clash/contactRuns.ts` for what is made of them. A saved group's run
// reads each GLB's members out (`glbMembers`) and measures them together (`findConvexContacts`).
import * as Comlink from "comlink";

import {loadEmscriptenModule} from "@/utils/wasm/emscriptenLoader";

const WASM_URL = "/wasm/adacpp_clash.js";

interface FailureRaw {
    i: number;
    j: number;
    reason: string;
}

/** What `findGlbContacts` returns (typed arrays already copied out of the wasm heap). */
export interface GlbContactsRaw {
    error?: string;
    memberIds: string[];
    memberNames: string[];
    pairs: Uint32Array;
    near1: Float64Array;
    near2: Float64Array;
    normal: Float64Array;
    penetrationDepth: Float64Array;
    contactArea: Float64Array;
    patchOffsets: Uint32Array;
    patchNormals: Float64Array;
    patchPointOffsets: Uint32Array;
    patchPoints1: Float64Array;
    patchPoints2: Float64Array;
    failures: FailureRaw[];
    candidatePairs: number;
    belowMinArea: number;
    degenerate: Uint32Array;
}

/** What `glbMembers` returns: a GLB's members, unmeasured, as the soup `findConvexContacts` takes. */
export interface GlbMembersRaw {
    error?: string;
    ids: string[];
    names: string[];
    vertices: Float64Array;
    vertexOffsets: Uint32Array;
    triangles: Uint32Array;
    triangleOffsets: Uint32Array;
}

/** What `findConvexContacts` returns: `findGlbContacts`' columns without the member names. */
export type ConvexContactsRaw = Omit<GlbContactsRaw, "memberIds" | "memberNames">;

export interface MeshSoupInput {
    vertices: Float64Array;
    vertexOffsets: Uint32Array;
    triangles: Uint32Array;
    triangleOffsets: Uint32Array;
}

interface EmModule {
    findGlbContacts(
        glb: Uint8Array,
        securityMargin: number,
        minContactArea: number,
        lineContactThickness: number,
        upAxis: string,
    ): GlbContactsRaw;
    /** Newer modules only -- feature-detected (see `requireSoupApi`). */
    glbMembers?(glb: Uint8Array, upAxis: string): GlbMembersRaw;
    findConvexContacts?(
        vertices: Float64Array,
        vertexOffsets: Uint32Array,
        triangles: Uint32Array,
        triangleOffsets: Uint32Array,
        securityMargin: number,
        minContactArea: number,
        lineContactThickness: number,
        hull: boolean,
    ): ConvexContactsRaw;
}

/** A module built before `glbMembers` cannot check several GLBs together; said so, not guessed at. */
function requireSoupApi(Module: EmModule): Required<Pick<EmModule, "glbMembers" | "findConvexContacts">> {
    if (typeof Module.glbMembers !== "function" || typeof Module.findConvexContacts !== "function") {
        throw new Error(
            "this viewer's adacpp_clash module has no glbMembers / findConvexContacts, so it cannot check the " +
                "models of a group together -- it needs a newer adacpp build",
        );
    }
    return {glbMembers: Module.glbMembers.bind(Module), findConvexContacts: Module.findConvexContacts.bind(Module)};
}

let modulePromise: Promise<EmModule> | null = null;

function getModule(): Promise<EmModule> {
    if (!modulePromise) modulePromise = loadEmscriptenModule<EmModule>(WASM_URL);
    return modulePromise;
}

export interface GlbContactsOptions {
    securityMargin: number;
    minContactArea: number;
    lineContactThickness: number;
    upAxis: "z" | "y";
}

const api = {
    /** Whether the module loads at all -- a viewer image without it simply cannot run the check here. */
    async available(): Promise<boolean> {
        try {
            await getModule();
            return true;
        } catch {
            modulePromise = null;
            return false;
        }
    },

    async findGlbContacts(glb: ArrayBuffer, opts: GlbContactsOptions): Promise<GlbContactsRaw & {ms: number}> {
        const Module = await getModule();
        const t0 = performance.now();
        const r = Module.findGlbContacts(
            new Uint8Array(glb),
            opts.securityMargin,
            opts.minContactArea,
            opts.lineContactThickness,
            opts.upAxis,
        );
        if (r.error) throw new Error(`clash check failed: ${r.error}`);
        return {...r, ms: performance.now() - t0};
    },

    async glbMembers(glb: ArrayBuffer, upAxis: "z" | "y"): Promise<GlbMembersRaw> {
        const {glbMembers} = requireSoupApi(await getModule());
        const r = glbMembers(new Uint8Array(glb), upAxis);
        if (r.error) throw new Error(`reading the GLB's members failed: ${r.error}`);
        return r;
    },

    async findConvexContacts(
        soup: MeshSoupInput,
        opts: Omit<GlbContactsOptions, "upAxis">,
    ): Promise<ConvexContactsRaw & {ms: number}> {
        const {findConvexContacts} = requireSoupApi(await getModule());
        const t0 = performance.now();
        const r = findConvexContacts(
            soup.vertices,
            soup.vertexOffsets,
            soup.triangles,
            soup.triangleOffsets,
            opts.securityMargin,
            opts.minContactArea,
            opts.lineContactThickness,
            true,
        );
        if (r.error) throw new Error(`clash check failed: ${r.error}`);
        return {...r, ms: performance.now() - t0};
    },
};

export type ClashContactsAPI = typeof api;
Comlink.expose(api);
