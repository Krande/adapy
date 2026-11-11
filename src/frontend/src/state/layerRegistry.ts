// The named layers drawn over the model that a person can switch on and off: a map under a robot, a
// planned path, a range scan. What draws a layer (an overlay of lines or points, a ground image)
// joins it here with a function that applies the layer's look; the Layers panel lists one row per
// layer and changes the look. Whether a layer is on and how opaque it is are kept per layer id in
// the browser, so they are as the user left them the next time the same layer is registered.
//
// No three.js in this module: both viewports and the plugin API use it.

export interface LayerInfo {
    /** Unique among the layers, and what its look is remembered by. */
    id: string;
    /** What the row is called. */
    label: string;
    /** Rows with the same group are listed together under it. */
    group?: string;
}

export interface LayerEntry extends LayerInfo {
    readonly visible: boolean;
    /** 0..1. */
    readonly opacity: number;
}

/** Applies a layer's look to something that draws it. */
export type LayerMember = (visible: boolean, opacity: number) => void;

interface Held {
    entry: LayerEntry;
    members: Set<LayerMember>;
}

const STORAGE_KEY = "ada-layer-look";

const layers = new Map<string, Held>();
const listeners = new Set<() => void>();
let listed: readonly LayerEntry[] | null = null;
let stored: Record<string, {visible?: boolean; opacity?: number}> | null = null;

function remembered(): Record<string, {visible?: boolean; opacity?: number}> {
    if (stored) return stored;
    stored = {};
    try {
        const text = globalThis.localStorage?.getItem(STORAGE_KEY);
        const parsed: unknown = text ? JSON.parse(text) : null;
        if (parsed && typeof parsed === "object") stored = parsed as typeof stored;
    } catch {
        // No storage here, or not ours to read: the layers start as they are registered.
    }
    return stored!;
}

function remember(entry: LayerEntry): void {
    remembered()[entry.id] = {visible: entry.visible, opacity: entry.opacity};
    try {
        globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(stored));
    } catch {
        // Storage full or refused: the look holds for this page.
    }
}

function changed(): void {
    listed = null;
    for (const listener of listeners) listener();
}

const clamp01 = (value: number): number => (Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 1);

/** Join layer `info.id` with something that draws it, making the layer when it is the first. The
 *  member is given the layer's look at once (the one the user left it with, when there is one) and
 *  again whenever it changes. Returns the function that takes the member out; the layer goes with
 *  its last member. */
export function joinLayer(info: LayerInfo, member: LayerMember, initial: {visible?: boolean; opacity?: number} = {}): () => void {
    let held = layers.get(info.id);
    if (!held) {
        const kept = remembered()[info.id];
        const entry: LayerEntry = {
            id: info.id,
            label: info.label,
            group: info.group,
            visible: typeof kept?.visible === "boolean" ? kept.visible : (initial.visible ?? true),
            opacity: clamp01(typeof kept?.opacity === "number" ? kept.opacity : (initial.opacity ?? 1)),
        };
        layers.set(info.id, (held = {entry, members: new Set()}));
        changed();
    }
    held.members.add(member);
    member(held.entry.visible, held.entry.opacity);
    const joined = held;
    return () => {
        if (!joined.members.delete(member) || joined.members.size > 0) return;
        if (layers.get(info.id) === joined) {
            layers.delete(info.id);
            changed();
        }
    };
}

function update(id: string, visible: boolean, opacity: number): void {
    const held = layers.get(id);
    if (!held || (held.entry.visible === visible && held.entry.opacity === opacity)) return;
    held.entry = {...held.entry, visible, opacity};
    remember(held.entry);
    for (const member of held.members) member(visible, opacity);
    changed();
}

export function setLayerVisible(id: string, visible: boolean): void {
    const held = layers.get(id);
    if (held) update(id, visible, held.entry.opacity);
}

export function setLayerOpacity(id: string, opacity: number): void {
    const held = layers.get(id);
    if (held) update(id, held.entry.visible, clamp01(opacity));
}

/** The layers in the order they were registered; the same array until one changes. */
export function listLayers(): readonly LayerEntry[] {
    return (listed ??= Array.from(layers.values(), (held) => held.entry));
}

export function layerCount(): number {
    return layers.size;
}

/** Called when a layer was added, removed or changed. Returns the unsubscribe function. */
export function subscribeLayers(listener: () => void): () => void {
    listeners.add(listener);
    return () => void listeners.delete(listener);
}

/** For tests: forget every layer and what was read from the browser's storage. */
export function resetLayerRegistry(): void {
    layers.clear();
    stored = null;
    changed();
}
