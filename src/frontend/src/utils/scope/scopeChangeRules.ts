import type {ConfirmRequest} from "@/ui/confirm";

// The decision and the wording for "switching scope will unload your model", with no
// store or scene imports.
//
// Split out for the same reason as commandFilter and coreProviderRules: anything that
// touches modelState pulls in the model worker (`?worker&inline`), which plain
// `node --test` cannot resolve. Keeping the rules here means the part worth asserting
// is testable and the wiring stays a thin shell.

export interface SceneContents {
    /** Source keys registered by the storage browser. Empty for models that arrived any
     *  other way. */
    sourceNames: ReadonlySet<string>;
    /** Groups actually in the three.js scene. The honest "is anything loaded". */
    sceneGroupCount: number;
}

/**
 * What a scope change would discard.
 *
 * Named sources are NOT sufficient on their own: only the storage browser registers
 * them, so a model from `?demo=1`, from a `.show()` push over the websocket, or from a
 * drag-and-drop has an empty source set and a full scene. Asking the source registry
 * alone made the guard silently do nothing in exactly the cases where the model was
 * hardest to get back.
 */
export function scopeChangeLoss(scene: SceneContents): {willDiscard: boolean; names: string[]} {
    const names = Array.from(scene.sourceNames);
    return {willDiscard: names.length > 0 || scene.sceneGroupCount > 0, names};
}

/** The confirmation copy. `names` may be empty even when something is loaded — see
 *  scopeChangeLoss — so there is a nameless variant. */
export function scopeChangeConfirmRequest(names: string[], targetScopeName: string): ConfirmRequest {
    let body: string[];
    if (names.length === 0) {
        body = [
            "The model currently in the viewer belongs to the scope you are leaving.",
            "Switching scope unloads it.",
        ];
    } else if (names.length === 1) {
        body = [
            `“${names[0]}” is loaded in the viewer and belongs to the scope you are leaving.`,
            "Switching scope unloads it. You can load it again from the file list.",
        ];
    } else {
        body = [
            `${names.length} models are loaded in the viewer and belong to the scope you are leaving.`,
            names.join(", "),
            "Switching scope unloads them. You can load them again from the file list.",
        ];
    }

    return {
        title: `Switch to ${targetScopeName}?`,
        body,
        // Says what happens, rather than "OK" — the whole point of the prompt is that
        // the consequence is not obvious from the control you touched.
        confirmLabel: "Switch and unload",
        cancelLabel: "Stay here",
        tone: "danger",
    };
}
