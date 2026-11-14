import type React from "react";

// Toasts a shell or a plugin adds to the app's one toast stack (ConversionProgress): its
// own notifications - a local tool's progress, say - in the same corner, the same stack
// and the same order as core's job and load toasts, instead of a second place on screen
// for the user to watch.
//
// A slot renders its own rows (or nothing); the stack places them above core's own.

export interface ToastSlotSpec {
    id: string;
    /** Order among the slots, lower first. Default 0. */
    order?: number;
    /** The slot's toasts, or null when it has none now. */
    render(): React.ReactNode;
}

const slots = new Map<string, ToastSlotSpec & {owner: string}>();
let version = 0;
const listeners = new Set<() => void>();

function changed(): void {
    version += 1;
    for (const cb of listeners) cb();
}

/** Add (or replace) a slot in the toast stack. */
export function registerToastSlot(spec: ToastSlotSpec, owner = "core"): void {
    slots.set(spec.id, {...spec, owner});
    changed();
}

export function unregisterToastSlot(id: string): void {
    if (slots.delete(id)) changed();
}

/** The slots, in order. */
export function listToastSlots(): ToastSlotSpec[] {
    return [...slots.values()].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.id.localeCompare(b.id));
}

export function subscribeToastSlots(cb: () => void): () => void {
    listeners.add(cb);
    return () => listeners.delete(cb);
}

export const toastSlotsVersion = (): number => version;
