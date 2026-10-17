// Main-thread handle on the convex-contact wasm worker (clashContacts.worker.ts). One worker, created
// on first use: the module is only fetched when someone actually runs a clash check in the browser.
import * as Comlink from "comlink";

import ClashContactsWorker from "./clashContacts.worker.ts?worker&inline";
import type {
    ClashContactsAPI,
    ConvexContactsRaw,
    GlbContactsOptions,
    GlbContactsRaw,
    GlbMembersRaw,
    MeshSoupInput,
} from "./clashContacts.worker";

export type {ConvexContactsRaw, GlbContactsOptions, GlbContactsRaw, GlbMembersRaw, MeshSoupInput};

let apiRemote: Comlink.Remote<ClashContactsAPI> | null = null;

function ensureApi(): Comlink.Remote<ClashContactsAPI> {
    if (!apiRemote) apiRemote = Comlink.wrap<ClashContactsAPI>(new ClashContactsWorker());
    return apiRemote;
}

/** Whether this viewer ships the `adacpp_clash` module. */
export function clashContactsAvailable(): Promise<boolean> {
    return ensureApi().available();
}

/** Measure the members of a viewer GLB. The buffer is transferred to the worker (it is detached here). */
export function findGlbContacts(glb: ArrayBuffer, opts: GlbContactsOptions): Promise<GlbContactsRaw & {ms: number}> {
    return ensureApi().findGlbContacts(Comlink.transfer(glb, [glb]), opts);
}

/** A viewer GLB's members as a mesh soup, unmeasured. The buffer is transferred to the worker. */
export function glbMembers(glb: ArrayBuffer, upAxis: "z" | "y"): Promise<GlbMembersRaw> {
    return ensureApi().glbMembers(Comlink.transfer(glb, [glb]), upAxis);
}

/** Measure a mesh soup, each mesh hulled -- members of several GLBs checked together. The soup's
 *  buffers are transferred to the worker. */
export function findConvexContacts(
    soup: MeshSoupInput,
    opts: Omit<GlbContactsOptions, "upAxis">,
): Promise<ConvexContactsRaw & {ms: number}> {
    const buffers = [soup.vertices.buffer, soup.vertexOffsets.buffer, soup.triangles.buffer, soup.triangleOffsets.buffer];
    return ensureApi().findConvexContacts(Comlink.transfer(soup, buffers as ArrayBuffer[]), opts);
}
