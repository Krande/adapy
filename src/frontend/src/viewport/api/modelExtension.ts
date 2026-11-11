// The `ADA_EXT_data` extension a viewport announced with a model (`LoadedModelInfo.adaExtData`, JSON
// text), as an object. The text can be large and several listeners read it, so it is parsed once per
// announcement. No three.js.
import type {LoadedModelInfo} from "./viewportApi";

const parsed = new WeakMap<LoadedModelInfo, unknown>();

/** The announced model's extension; null when it has none, or its text is not JSON. */
export function announcedExtension(info: LoadedModelInfo): unknown | null {
    if (!info.adaExtData) return null;
    if (parsed.has(info)) return parsed.get(info) ?? null;
    let extension: unknown = null;
    try {
        extension = JSON.parse(info.adaExtData);
    } catch (err) {
        console.warn("[viewport] the model's ADA_EXT_data extension is not JSON", err);
    }
    parsed.set(info, extension);
    return extension;
}
