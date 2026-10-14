// Main-thread handle on the convex-contact wasm worker (clashContacts.worker.ts). One worker, created
// on first use: the module is only fetched when someone actually runs a clash check in the browser.
import * as Comlink from "comlink";

import ClashContactsWorker from "./clashContacts.worker.ts?worker&inline";
import type {ClashContactsAPI, GlbContactsOptions, GlbContactsRaw} from "./clashContacts.worker";

export type {GlbContactsOptions, GlbContactsRaw};

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
