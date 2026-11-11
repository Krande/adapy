// The `ADA_EXT_data` extension of each loaded model, by the source it was loaded from, for a viewport
// that builds no scene objects to keep it on. The three.js loader keeps a model's extension on its
// scene group; the three.js-free viewport hands the text over once, and it is kept here. No three.js.

const extensions = new Map<string, unknown>();
const listeners = new Set<() => void>();

/** Keep (or with null drop) the extension of the model loaded from `sourceName`. */
export function setSourceExtension(sourceName: string, extension: unknown | null): void {
    if (extension == null) {
        if (!extensions.delete(sourceName)) return;
    } else {
        extensions.set(sourceName, extension);
    }
    listeners.forEach((l) => l());
}

export function sourceExtension(sourceName: string): unknown | null {
    return extensions.get(sourceName) ?? null;
}

/** Forget every source: the models on screen are being replaced. Says nothing to the listeners,
 *  who hear of the model that replaces them. */
export function clearSourceExtensions(): void {
    extensions.clear();
}

/** Called when an extension was kept or dropped. Returns the unsubscribe function. */
export function onSourceExtensions(listener: () => void): () => void {
    listeners.add(listener);
    return () => void listeners.delete(listener);
}
