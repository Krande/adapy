// A signal for the parts of the scene info a store does not hold (a model's visibility is
// a flag on its three.js group), so the mirror (`./sceneInfoMirror`) learns when one changed.
// Dependency-free.

const listeners = new Set<() => void>();

/** Something the scene info shows changed outside a store. */
export function notifySceneInfoChanged(): void {
    for (const l of [...listeners]) l();
}

export function onSceneInfoChanged(cb: () => void): () => void {
    listeners.add(cb);
    return () => void listeners.delete(cb);
}
