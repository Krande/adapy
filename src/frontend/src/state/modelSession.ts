// The currently loaded model, as one object instead of four module-level
// singletons.
//
// Loading a model used to scatter that model's identity across the codebase:
// the source key in `modelState`, the per-source scene groups in a `Map`
// deliberately parked outside that store, the streaming-FEA handle in a
// `let active` inside the 1,800-line FEA loader, and the colour-owner stack in
// its own store. Nothing tied them together, so every teardown path had to
// remember all four by hand and a forgotten one left a stale pointer into a
// model that was no longer on screen — a "go to node" marker on a vanished
// vertex, a field view restored over a different analysis.
//
// A `ModelSession` is that identity: what is loaded, where its bytes came
// from, which groups it put in the scene, its FEA handle if it has one, and
// the colour-owner stack its paints are arbitrated by. `open()` begins one and
// `close()` ends one; closing drops every reference the session held together.
//
// What it deliberately does NOT own: `feaAnimationStore` (the user's field
// selection, step and warp) and `colorLegendStore` (the shared legend). Those
// are view state that outlives a model on purpose — pick a field, swap the
// model, keep the field — so the session references the arbiter that decides
// whose view is whose rather than absorbing the stores themselves.

import * as THREE from "three";
import { create } from "zustand";

import { useSceneColorOwnerStore } from "./sceneColorOwnerStore";
import type { ParsedBeamSolidsWarp } from "@/services/feaBeamSolidsWarp";
import type { FeaManifest } from "@/services/viewerApi";

/** Which load pipeline produced the session's model. */
export type ModelSessionKind = "cad" | "fea";

/** What is loaded, and where it came from. */
export interface ModelSessionIdentity {
    /** Durable storage key / filename the viewer shows as loaded. Null for a
     *  session opened before its name is known (a websocket REPLACE names the
     *  model only after the bytes land). */
    sourceName: string | null;
    /** Where the bytes were read from: a `blob:` URL, a presigned object-store
     *  GET, or the authed `/blobs/{key}` GET. Transient by nature — a blob URL
     *  is revoked right after the load — so it identifies the load, not the
     *  model. */
    url: string | null;
    kind: ModelSessionKind;
}

/**
 * Cached state for the currently-rendered FEA streaming source. Lets the
 * picker re-apply with a different (component, step) on slider drag without
 * re-fetching the mesh GLB or the field blob — switching steps within a single
 * field becomes a synchronous in-memory operation.
 *
 * Lived as `let active` in `utils/scene/handlers/load_fea_streaming` until it
 * moved here; that module still holds the only code that builds one.
 */
export interface FeaSessionHandle {
    sourceName: string;
    manifest: FeaManifest;
    /** The THREE mesh whose geometry we deform. */
    mesh: THREE.Mesh;
    /** Snapshot of the mesh's original positions, used to compute
     * displacement-from-base on every step change. */
    basePositions: Float32Array;
    /** The bake's element-edge index, kept so the undeformed reference wireframe
     *  can be built and rebuilt without re-fetching the sidecar. */
    edgeIndices?: Uint32Array;
    /** Optional beam-solid mesh — present when the manifest carries
     *  ``beam_solids_url``. Hosts beam (line) elements tessellated as
     *  3D extruded sections. Shares the FEA root group with the main
     *  mesh; the AFEL element-field path paints both meshes since
     *  beam labels live in both ``drawRanges`` maps (with a zero-
     *  triangle range on the main mesh and a real range here).
     *  No warp on this mesh in v1 — vertices aren't nodal. */
    beamSolidMesh?: THREE.Mesh;
    /** Base positions for the beam-solid mesh, snapshot at load. The
     *  AFEL kernel resets the position attribute to this snapshot
     *  before re-painting, mirroring the main-mesh path. */
    beamSolidBasePositions?: Float32Array;
    /** AFBV warp mapping — per-vertex (node0_idx, node1_idx, t). Used
     *  to lerp nodal displacements onto the solid mesh's vertices so
     *  the solid beams stay connected to the rest of the structure
     *  under any morph-scale factor. */
    beamSolidWarp?: ParsedBeamSolidsWarp;
}

/** One loaded model and everything the viewer holds on its behalf. */
export interface ModelSession {
    /** Mutable: a session opened before its name is known is named when the
     *  first group registers, and the FEA loader upgrades `kind`. */
    identity: ModelSessionIdentity;
    /**
     * Source key → scene group, for every model overlaid in this session. A
     * plain mutable `Map` rather than store state, for the reason it was kept
     * off `modelState` to begin with: nothing re-renders off THREE.Object3D
     * pointers, and the immutability penalty would be paid on every load. The
     * *names* live on `modelState.loadedSourceNames`, which is what the
     * storage-list checkboxes read.
     */
    groups: Map<string, THREE.Group>;
    /** The streaming-FEA handle, or null for a session that is not an FEA
     *  result. Written once by the FEA loader and mutated in place by it. */
    fea: FeaSessionHandle | null;
    /** The colour-owner stack arbitrating this session's paints. A reference,
     *  not a copy: the stack is a page-lifetime store whose *saved views* are
     *  what belongs to a session, and `close()` is what drops them. */
    colorOwner: typeof useSceneColorOwnerStore;
}

export interface ModelSessionState {
    /** The open session, or null when no model is loaded. */
    session: ModelSession | null;

    /**
     * Begin a session for `identity`, replacing any open one. Group refs and
     * the FEA handle of the previous session go with it — the scene is being
     * replaced, so they are about to be invalid — while the colour-owner
     * stack's saved views are left alone: a load is not a teardown, and
     * whether the incoming field belongs to the user or to an owning mode is
     * the arbiter's call, not this store's.
     */
    open: (identity: Partial<ModelSessionIdentity>) => ModelSession;

    /**
     * End the open session. Everything it held goes at once, the colour-owner
     * stack's saved views included: they all referred to a model that is no
     * longer on screen, so whatever loads next is a NEW source even when it is
     * the same file again. No-op when nothing is open, except that the owner
     * views are still reset (teardown paths call this unconditionally).
     */
    close: () => void;

    /** The open session, or null. */
    current: () => ModelSession | null;

    /**
     * The open session, opening an anonymous one if there is none. For the
     * load paths that register a group before anybody named the model.
     */
    ensure: () => ModelSession;
}

function newSession(identity: Partial<ModelSessionIdentity>): ModelSession {
    return {
        identity: {
            sourceName: identity.sourceName ?? null,
            url: identity.url ?? null,
            kind: identity.kind ?? "cad",
        },
        groups: new Map<string, THREE.Group>(),
        fea: null,
        colorOwner: useSceneColorOwnerStore,
    };
}

export const useModelSessionStore = create<ModelSessionState>((set, get) => ({
    session: null,

    open: (identity) => {
        const session = newSession(identity);
        set({ session });
        return session;
    },

    close: () => {
        const open = get().session;
        if (open) {
            // Drop the references before the object itself, so anything still
            // holding the session (a load in flight) sees an empty one rather
            // than a live pointer into a scene that is gone.
            open.groups.clear();
            open.fea = null;
        }
        useSceneColorOwnerStore.getState().clearViews();
        set({ session: null });
    },

    current: () => get().session,

    ensure: () => get().session ?? get().open({}),
}));

/** The open session's group map, or an empty one. Reads only. */
const NO_GROUPS: Map<string, THREE.Group> = new Map();

function peekGroups(): Map<string, THREE.Group> {
    return useModelSessionStore.getState().session?.groups ?? NO_GROUPS;
}

/**
 * The session's source-key → group map, as a stable object the modules that
 * read it can keep importing.
 *
 * The map itself now belongs to whichever session is open, so it cannot be a
 * module-level `Map` any more; this is a thin live view onto the open one.
 * Reads against no session see an empty map; a write opens a session, which is
 * what the websocket REPLACE path relies on (it registers a group without
 * naming the model first).
 */
export const loadedSourceGroups = {
    get size(): number {
        return peekGroups().size;
    },
    get(name: string): THREE.Group | undefined {
        return peekGroups().get(name);
    },
    has(name: string): boolean {
        return peekGroups().has(name);
    },
    set(name: string, group: THREE.Group): void {
        useModelSessionStore.getState().ensure().groups.set(name, group);
    },
    delete(name: string): boolean {
        return peekGroups().delete(name);
    },
    clear(): void {
        peekGroups().clear();
    },
    keys(): IterableIterator<string> {
        return peekGroups().keys();
    },
    values(): IterableIterator<THREE.Group> {
        return peekGroups().values();
    },
    entries(): IterableIterator<[string, THREE.Group]> {
        return peekGroups().entries();
    },
    [Symbol.iterator](): IterableIterator<[string, THREE.Group]> {
        return peekGroups()[Symbol.iterator]();
    },
};
