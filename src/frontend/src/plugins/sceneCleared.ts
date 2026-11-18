// `ctx.scene.onSceneCleared` (plugin API 1.30.0): a plugin told when the scene is emptied, so it can
// put its own state back (what it drew, what it holds of the models that went). Told by the scene's
// one teardown, `clearScene` (utils/scene/handlers/clear_scene.ts), once per clear, whatever asked
// for it.
//
// Dependency-free: no store, no three.js, nothing the registry's plain-node tests cannot load.

import type {SceneCleared} from "./registry";

type Listener = (cleared: SceneCleared) => void;

const listeners = new Set<Listener>();

/** Call `listener` after each clear of the scene; the returned function stops that. Subscribing
 *  the same function twice calls it twice, and each unsubscribe ends its own. */
export function onSceneCleared(listener: Listener): () => void {
    const entry: Listener = (cleared) => listener(cleared);
    listeners.add(entry);
    return () => void listeners.delete(entry);
}

/** Tell every listener the scene was cleared. One that throws is logged and the others are still told. */
export function notifySceneCleared(cleared: SceneCleared): void {
    for (const listener of [...listeners]) {
        try {
            listener(cleared);
        } catch (err) {
            console.error("[plugins] an onSceneCleared listener failed", err);
        }
    }
}
