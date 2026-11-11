import type {Billboard, BillboardLayer, Point3} from "@/viewport/api/billboards";
import type {ObjectRef} from "@/viewport/api/viewportApi";
import {textLabelImage} from "@/utils/viewer/iconImages";

// Name labels over the selected objects, as plain data: which object, its text and where it goes.
// The viewport on screen says where an object is and draws the labels; nothing here knows which.

/** The most name labels drawn at once: beyond a few hundred they cover the model and each other. */
export const NAME_LABEL_CAP = 200;
/** A name label's height on screen, in CSS pixels. */
export const NAME_LABEL_HEIGHT_PX = 18;
/** Over the type icons, which sit in the model rather than on an object. */
const NAME_LABEL_ORDER = 9996;

export interface NameLabel {
    /** The object it is of: its model's key and its id, as one string. */
    key: string;
    text: string;
    position: Point3;
}

/** The labels for `refs`, the first `cap` of them that are drawn: each object once, named by `nameOf`
 *  (its id when it has no name), placed where `anchorOf` says it is. */
export function nameLabels(
    refs: readonly ObjectRef[],
    nameOf: (ref: ObjectRef) => string | null | undefined,
    anchorOf: (ref: ObjectRef) => Point3 | null,
    cap = NAME_LABEL_CAP,
): NameLabel[] {
    const out: NameLabel[] = [];
    const seen = new Set<string>();
    for (const ref of refs) {
        if (out.length >= cap) break;
        const key = `${ref.model}\n${ref.id}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const position = anchorOf(ref);
        if (!position) continue;
        out.push({key, text: nameOf(ref) || ref.id, position});
    }
    return out;
}

const billboardOf = (label: NameLabel): Billboard => ({
    position: label.position,
    image: textLabelImage(label.text),
    height: NAME_LABEL_HEIGHT_PX,
    sizing: "screen",
    order: NAME_LABEL_ORDER,
});

/** Make `layer` show `next`, given that it shows `shown` (which is updated): a label that stays keeps
 *  its billboard, and is touched only if its text or place changed. */
export function showNameLabels(layer: BillboardLayer, shown: Map<string, {handle: number; label: NameLabel}>, next: readonly NameLabel[]): void {
    const keep = new Set<string>();
    for (const label of next) {
        keep.add(label.key);
        const had = shown.get(label.key);
        if (!had) {
            shown.set(label.key, {handle: layer.add(billboardOf(label)), label});
        } else if (had.label.text !== label.text || had.label.position.some((v, i) => v !== label.position[i])) {
            layer.update(had.handle, billboardOf(label));
            had.label = label;
        }
    }
    for (const [key, had] of shown) {
        if (keep.has(key)) continue;
        layer.remove(had.handle);
        shown.delete(key);
    }
}
