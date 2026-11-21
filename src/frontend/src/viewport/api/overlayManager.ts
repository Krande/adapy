// What is drawn OVER the scene, by every module that draws there: one registry, so a clear of the
// scene, a model leaving it, a renderer switch and an owner going away reach every overlay the same
// way, and none can be missed.
//
// An overlay is an ENTRY: who owns it, what it is called, how long it lives (`OverlayLifetime`), what
// a replace does to it (`ReplacePolicy`), and how to draw it on a viewport. The manager keeps it on
// whichever viewport is on screen: an entry's layers are made through its handle on the viewport it
// is attached to, and released when it leaves that viewport; on a renderer switch the entry is drawn
// again on the one that came up (its `draw`). The object marks of every entry are merged into the one
// map a viewport draws (`setObjectMarks`; where two entries mark one object, the newer entry's colour
// shows), and the fade of the rest has one owner at a time (`setObjectFade`).
//
// LIFETIMES.
//   * `view`: a feature's standing view of a store (the clash check's markers, the measurements). It
//     is never removed by a clear: the clear tells its store to forget (`forget`), and the store's own
//     redraw empties it. Without a `forget`, it is drawn again.
//   * `scene`: removed by a clear (a replace keeps it when its policy says so).
//   * `model`: removed by a clear, and as soon as one of the models it is drawn over leaves the scene.
//   * `fea-result`: drawn in an FEA result's coordinates; removed when that result goes
//     (`removeWhere`), by a clear, and by a renderer switch (the result is drawn anew there).
//   * `until-removed`: a tool's (a placed box): only its owner removes it.
// A removed entry's handle stays safe to call: everything it does is nothing.
//
// No three.js in this module or in anything it imports.

import {requestRender} from "@/state/perfStore";
import type {BillboardLayer} from "./billboards";
import type {ImageLayer} from "./imageLayer";
import type {ObjectMarks} from "./objectColors";
import type {ObjectFade} from "./objectFade";
import type {OverlayLayer} from "./overlayLayer";
import {activeViewport, onActiveViewportChange, type ObjectRef, type ViewportApi} from "./viewportApi";

export type OverlayLifetime =
    | {kind: "view"}
    | {kind: "scene"}
    | {kind: "model"; sources: readonly string[]}
    | {kind: "fea-result"; token: object}
    | {kind: "until-removed"};

/** What a clear that is a replace (a load putting a model in place of what is cleared, a renderer
 *  switch handing the models over) does to an entry: leave it, remove it, or (a view) tell its store
 *  to forget. Every other clear removes, or forgets, whatever the policy. */
export type ReplacePolicy = "keep" | "remove" | "forget";

export interface OverlaySpec {
    /** Who drew it: a plugin's owner, or `core:<feature>`. `removeOwner` takes all of an owner's. */
    owner: string;
    /** What it is, within its owner (for a list of what is drawn, and a scene state later). */
    id?: string;
    /** A tag for `removeOwner(owner, kind)`: an owner's highlights apart from its drawings. */
    kind?: string;
    lifetime: OverlayLifetime;
    /** Default "keep". */
    onReplace?: ReplacePolicy;
    /** Draw on the viewport the entry is attached to (`handle.viewport`), through the handle's
     *  layers: run when it is attached -- on registering, on another viewport taking over, on being
     *  shown again -- and on `redraw()`. What it returns is run before the next draw and when the entry
     *  leaves the viewport; the layers themselves are released by the manager. */
    draw?: (handle: OverlayHandle) => void | (() => void);
    /** The store this overlay is drawn from forgets what it holds: a clear must not leave a store
     *  that would just draw it again (the clash check's result, `forgetResult`). */
    forget?: () => void;
    /** Run once, when the entry is removed (by whichever route), after it left the viewport. */
    onRemoved?: () => void;
}

export interface OverlayHandle {
    /** False once removed: every call does nothing then. */
    readonly alive: boolean;
    readonly visible: boolean;
    /** The viewport the entry is drawn on now; null when none is on screen (or it is hidden). */
    readonly viewport: ViewportApi | null;
    /** The viewport's layer called `name` for this entry, made on first use and held until the
     *  entry leaves the viewport; null when it is not drawn on one. Layers are shared between entries
     *  that ask for the same name, and disposed with the last one. */
    lines(name: string): OverlayLayer | null;
    labels(name: string): BillboardLayer | null;
    images(name: string): ImageLayer | null;
    /** Mark `refs` in `color` (sRGB hex), in place of what this entry marked before; [] marks
     *  nothing and keeps the entry in the marks. */
    setMarks(refs: Iterable<ObjectRef>, color: string): void;
    /** Fade the rest as `fade` says, taking the fade from whoever had it; null lets go of it. */
    setFade(fade: ObjectFade | null): void;
    /** Draw again on the viewport it is on (`draw`). */
    redraw(): void;
    /** Hidden, it leaves the viewport (its layers released, its marks and fade withdrawn) and is
     *  drawn again when shown. */
    setVisible(visible: boolean): void;
    /** The models a `model` entry is drawn over. */
    setSources(sources: readonly string[]): void;
    remove(): void;
}

export interface OverlayInfo {
    owner: string;
    id: string | null;
    kind: string | null;
    lifetime: OverlayLifetime;
    visible: boolean;
}

// ----- the layers of each viewport, shared by name ------------------------------------------

type LayerKind = "lines" | "labels" | "images";
type AnyLayer = OverlayLayer | BillboardLayer | ImageLayer;

const shared = new WeakMap<ViewportApi, Map<string, {layer: AnyLayer; users: number}>>();

function takeLayer(viewport: ViewportApi, kind: LayerKind, name: string): AnyLayer {
    let held = shared.get(viewport);
    if (!held) shared.set(viewport, (held = new Map()));
    const key = `${kind}\n${name}`;
    let entry = held.get(key);
    if (!entry) {
        const layer = kind === "lines" ? viewport.overlays(name) : kind === "labels" ? viewport.billboards(name) : viewport.images(name);
        held.set(key, (entry = {layer, users: 0}));
    }
    entry.users++;
    return entry.layer;
}

function giveLayer(viewport: ViewportApi, kind: LayerKind, name: string): void {
    const held = shared.get(viewport);
    const key = `${kind}\n${name}`;
    const entry = held?.get(key);
    if (!entry || --entry.users > 0) return;
    held!.delete(key);
    try {
        entry.layer.dispose();
    } catch {
        // Its viewport is gone, and the layer with it.
    }
}

// ----- the entries --------------------------------------------------------------------------

interface Entry {
    spec: OverlaySpec;
    lifetime: OverlayLifetime;
    alive: boolean;
    visible: boolean;
    viewport: ViewportApi | null;
    layers: Map<string, {kind: LayerKind; name: string; layer: AnyLayer}>;
    cleanup: (() => void) | null;
    marks: {refs: ObjectRef[]; color: string} | null;
    drawing: boolean;
    handle: OverlayHandle;
}

const entries = new Set<Entry>();

function safely(what: string, run: () => void): void {
    try {
        run();
    } catch (err) {
        // eslint-disable-next-line no-console
        console.error(`[overlays] ${what} failed`, err);
    }
}

function runCleanup(entry: Entry): void {
    const cleanup = entry.cleanup;
    entry.cleanup = null;
    if (cleanup) {
        try {
            cleanup();
        } catch {
            // What it drew was on a viewport that is gone.
        }
    }
}

/** Take the entry off the viewport it is on: what its draw returned, then its layers. */
function detach(entry: Entry): void {
    runCleanup(entry);
    const viewport = entry.viewport;
    entry.viewport = null;
    if (!viewport) return;
    for (const {kind, name} of entry.layers.values()) giveLayer(viewport, kind, name);
    entry.layers.clear();
}

function draw(entry: Entry): void {
    if (!entry.spec.draw || !entry.viewport || entry.drawing) return;
    runCleanup(entry);
    entry.drawing = true;
    try {
        const cleanup = entry.spec.draw(entry.handle);
        entry.cleanup = typeof cleanup === "function" ? cleanup : null;
    } catch (err) {
        // eslint-disable-next-line no-console
        console.error(`[overlays] drawing ${entry.spec.owner}${entry.spec.id ? `/${entry.spec.id}` : ""} failed`, err);
    } finally {
        entry.drawing = false;
    }
}

/** Put the entry on the viewport on screen, when it is not there already: off the one it was on,
 *  and drawn on the new one. An FEA result's entry does not move: it is removed instead. */
function follow(entry: Entry): void {
    const now = activeViewport();
    if (!entry.alive || !entry.visible || entry.viewport === now) return;
    if (entry.viewport && entry.lifetime.kind === "fea-result") {
        remove(entry);
        return;
    }
    detach(entry);
    if (!now) return;
    entry.viewport = now;
    draw(entry);
}

function layerOf(entry: Entry, kind: LayerKind, name: string): AnyLayer | null {
    if (!entry.alive || !entry.visible) return null;
    // Asked before the manager heard of a viewport change: move first, so the layer is of the one on screen.
    if (entry.viewport !== activeViewport()) follow(entry);
    const viewport = entry.alive ? entry.viewport : null;
    if (!viewport) return null;
    const key = `${kind}\n${name}`;
    let held = entry.layers.get(key);
    if (!held) entry.layers.set(key, (held = {kind, name, layer: takeLayer(viewport, kind, name)}));
    return held.layer;
}

function remove(entry: Entry): void {
    if (!entry.alive) return;
    entry.alive = false;
    entries.delete(entry);
    detach(entry);
    if (entry.marks) pushMarks();
    if (fadeOwner === entry) releaseFade();
    if (entry.spec.onRemoved) safely("onRemoved", entry.spec.onRemoved);
    requestRender();
}

// ----- marks and fade -----------------------------------------------------------------------

/** Every entry's marks as the viewport on screen draws them: newest entry last, so its colour wins. */
function mergedMarks(): ObjectMarks {
    const out = new Map<string, Map<string, string>>();
    for (const entry of entries) {
        if (!entry.marks || !entry.visible) continue;
        for (const {model, id} of entry.marks.refs) {
            let ids = out.get(model);
            if (!ids) out.set(model, (ids = new Map()));
            ids.set(id, entry.marks.color);
        }
    }
    return out;
}

const anyMarks = (): boolean => [...entries].some((e) => e.marks !== null);

function pushMarks(): void {
    activeViewport()?.setObjectMarks?.(mergedMarks());
    requestRender();
}

let fadeOwner: Entry | null = null;
let fadeValue: ObjectFade | null = null;
/** The viewport the fade was put on, to take it off that one even when another has come since. */
let fadedOn: ViewportApi | null = null;

function unfade(): void {
    const viewport = fadedOn;
    fadedOn = null;
    if (!viewport) return;
    try {
        viewport.setObjectFade?.(null);
    } catch {
        // Its renderer is gone, and what it faded with it.
    }
}

function applyFade(): void {
    const viewport = activeViewport();
    if (fadedOn && fadedOn !== viewport) unfade();
    if (!fadeValue || !viewport?.setObjectFade) return;
    viewport.setObjectFade(fadeValue);
    fadedOn = viewport;
}

function releaseFade(): void {
    fadeOwner = null;
    fadeValue = null;
    unfade();
}

// ----- the viewport on screen ---------------------------------------------------------------

function viewportChanged(): void {
    for (const entry of [...entries]) follow(entry);
    if (anyMarks()) pushMarks();
    if (fadeValue) applyFade();
    else unfade();
}

let following = false;
function ensureFollowing(): void {
    if (following) return;
    following = true;
    onActiveViewportChange(viewportChanged);
}
// At once, so the manager hears of a new viewport before the modules that draw through it do.
ensureFollowing();

// ----- the manager --------------------------------------------------------------------------

export function register(spec: OverlaySpec): OverlayHandle {
    ensureFollowing();
    const entry: Entry = {
        spec,
        lifetime: spec.lifetime,
        alive: true,
        visible: true,
        viewport: null,
        layers: new Map(),
        cleanup: null,
        marks: null,
        drawing: false,
        handle: null as unknown as OverlayHandle,
    };
    const live = <A extends unknown[]>(fn: (...args: A) => void) => (...args: A): void => {
        if (entry.alive) fn(...args);
    };
    entry.handle = {
        get alive() {
            return entry.alive;
        },
        get visible() {
            return entry.visible;
        },
        get viewport() {
            return entry.alive ? entry.viewport : null;
        },
        lines: (name) => layerOf(entry, "lines", name) as OverlayLayer | null,
        labels: (name) => layerOf(entry, "labels", name) as BillboardLayer | null,
        images: (name) => layerOf(entry, "images", name) as ImageLayer | null,
        setMarks: live((refs: Iterable<ObjectRef>, color: string) => {
            entry.marks = {refs: Array.from(refs, ({model, id}) => ({model, id})), color};
            pushMarks();
        }),
        setFade: live((fade: ObjectFade | null) => {
            if (!fade) {
                if (fadeOwner === entry) releaseFade();
                return;
            }
            fadeOwner = entry;
            fadeValue = fade;
            if (entry.visible) applyFade();
        }),
        redraw: live(() => {
            if (!entry.visible) return;
            if (entry.viewport !== activeViewport()) follow(entry);
            else draw(entry);
            requestRender();
        }),
        setVisible: live((visible: boolean) => {
            if (entry.visible === visible) return;
            entry.visible = visible;
            if (!visible) {
                detach(entry);
                if (fadeOwner === entry) unfade();
            } else {
                follow(entry);
                if (fadeOwner === entry) applyFade();
            }
            if (entry.marks) pushMarks();
            requestRender();
        }),
        setSources: live((sources: readonly string[]) => {
            if (entry.lifetime.kind === "model") entry.lifetime = {kind: "model", sources: [...sources]};
        }),
        remove: () => remove(entry),
    };
    entries.add(entry);
    follow(entry);
    requestRender();
    return entry.handle;
}

/** Remove every entry of `owner` (of `kind`, when given): `ctx.scene.remove(owner)`. */
export function removeOwner(owner: string, kind?: string): void {
    removeWhere((info) => info.owner === owner && (kind === undefined || info.kind === kind));
}

/** Remove every entry `which` says: an FEA result's when it goes. */
export function removeWhere(which: (info: OverlayInfo) => boolean): void {
    for (const entry of [...entries]) if (which(infoOf(entry))) remove(entry);
}

/** What a clear of the scene does to what is drawn over it (`clearScene`, the one caller). */
export function clear(options: {replacing: boolean}): void {
    for (const entry of [...entries]) {
        if (!entry.alive) continue;
        const kind = entry.lifetime.kind;
        if (kind === "until-removed") continue;
        const policy: ReplacePolicy = options.replacing ? (entry.spec.onReplace ?? "keep") : kind === "view" ? "forget" : "remove";
        if (policy === "keep") continue;
        if (kind === "view") {
            if (entry.spec.forget) safely("forget", entry.spec.forget);
            else entry.handle.redraw();
            continue;
        }
        if (entry.spec.forget) safely("forget", entry.spec.forget);
        remove(entry);
    }
    requestRender();
}

/** The models loaded now: a `model` entry over one that is not goes (`followLoadedSources`). */
export function sourcesChanged(loaded: ReadonlySet<string>): void {
    for (const entry of [...entries]) {
        const lifetime = entry.lifetime;
        if (lifetime.kind !== "model" || lifetime.sources.every((s) => loaded.has(s))) continue;
        if (entry.spec.forget) safely("forget", entry.spec.forget);
        remove(entry);
    }
}

let sourcesBound = false;
/** Hear of the loaded models through `subscribe` (once: later calls do nothing). The model store
 *  imports three.js, so it is handed in rather than imported (utils/scene/overlaySources.ts). */
export function followLoadedSources(subscribe: (changed: (loaded: ReadonlySet<string>) => void) => () => void): void {
    if (sourcesBound) return;
    sourcesBound = true;
    subscribe(sourcesChanged);
}

function infoOf(entry: Entry): OverlayInfo {
    return {owner: entry.spec.owner, id: entry.spec.id ?? null, kind: entry.spec.kind ?? null, lifetime: entry.lifetime, visible: entry.visible};
}

/** What is drawn over the scene now, in the order it was registered. */
export function list(): OverlayInfo[] {
    return [...entries].map(infoOf);
}

/** The manager as one object: what modules import. */
export const sceneOverlays = {register, removeOwner, removeWhere, clear, sourcesChanged, followLoadedSources, list};
