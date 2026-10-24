/**
 * Selection boxes as kept in this browser: the stored shape, its validation and the label
 * counter that must not collide with restored boxes. Pure, node-testable.
 *
 * Boxes are world-space, and world space is the models' own coordinates shifted by the scene's
 * recentring offset. The offset the boxes were expressed in is stored with them, so a later
 * scene with another offset can move them back over the same geometry.
 */

import type {BoxSelectMode, Vec3} from "./boxMath";

export interface StoredBox {
    id: string;
    label: string;
    min: Vec3;
    max: Vec3;
    mode: BoxSelectMode;
    color: string;
    opacity: number;
    visible: boolean;
}

export interface StoredBoxes {
    v: 1;
    /** The recentring offset the boxes are expressed in; null when unknown. */
    offset: Vec3 | null;
    boxes: StoredBox[];
    activeId: string | null;
}

const KEY_PREFIX = "ada.bboxSelection:";

export const bboxStorageKey = (scope: string): string => `${KEY_PREFIX}${scope}`;

export const EMPTY_STORED_BOXES: StoredBoxes = {v: 1, offset: null, boxes: [], activeId: null};

function isRecord(v: unknown): v is Record<string, unknown> {
    return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function isVec3(v: unknown): v is Vec3 {
    return Array.isArray(v) && v.length === 3 && v.every((n) => typeof n === "number" && Number.isFinite(n));
}

/** One stored box, or null when it is not the known shape. */
export function parseStoredBox(raw: unknown): StoredBox | null {
    if (!isRecord(raw)) return null;
    const {id, label, min, max, mode, color, opacity, visible} = raw;
    if (typeof id !== "string" || !id) return null;
    if (!isVec3(min) || !isVec3(max)) return null;
    if (min.some((m, k) => m > max[k])) return null;
    if (mode !== "partial" && mode !== "full") return null;
    if (typeof color !== "string" || !/^#[0-9a-f]{6}$/i.test(color)) return null;
    if (typeof opacity !== "number" || !Number.isFinite(opacity)) return null;
    return {
        id,
        label: typeof label === "string" && label ? label : id,
        min: [min[0], min[1], min[2]],
        max: [max[0], max[1], max[2]],
        mode,
        color,
        opacity: Math.min(1, Math.max(0, opacity)),
        visible: visible !== false,
    };
}

/** The stored boxes in `raw`. Never throws: malformed entries and duplicate ids are dropped, and
 *  an active id naming no kept box becomes null. */
export function parseStoredBoxes(raw: string | null): StoredBoxes {
    if (!raw) return EMPTY_STORED_BOXES;
    let doc: unknown;
    try {
        doc = JSON.parse(raw);
    } catch {
        return EMPTY_STORED_BOXES;
    }
    if (!isRecord(doc) || doc.v !== 1 || !Array.isArray(doc.boxes)) return EMPTY_STORED_BOXES;
    const seen = new Set<string>();
    const boxes: StoredBox[] = [];
    for (const r of doc.boxes) {
        const b = parseStoredBox(r);
        if (!b || seen.has(b.id)) continue;
        seen.add(b.id);
        boxes.push(b);
    }
    const activeId = typeof doc.activeId === "string" && seen.has(doc.activeId) ? doc.activeId : null;
    return {v: 1, offset: isVec3(doc.offset) ? doc.offset : null, boxes, activeId};
}

export function serialiseStoredBoxes(s: Omit<StoredBoxes, "v">): string {
    return JSON.stringify({
        v: 1,
        offset: s.offset,
        boxes: s.boxes.map(({id, label, min, max, mode, color, opacity, visible}) => ({id, label, min, max, mode, color, opacity, visible})),
        activeId: s.activeId,
    });
}

/** The highest number a box's label ("... N") or id ("...-N") ends in; 0 when none does. A new
 *  box counts on from here, so its label and id are not those of a restored one. */
export function highestBoxNumber(boxes: readonly Pick<StoredBox, "id" | "label">[]): number {
    let n = 0;
    for (const b of boxes) {
        for (const m of [/(\d+)\s*$/.exec(b.label), /-(\d+)$/.exec(b.id)]) {
            const v = m ? Number(m[1]) : 0;
            if (Number.isSafeInteger(v) && v > n) n = v;
        }
    }
    return n;
}

/** `boxes` moved by `delta`. */
export function shiftedBoxes<B extends {min: Vec3; max: Vec3}>(boxes: readonly B[], delta: Vec3): B[] {
    return boxes.map((b) => ({
        ...b,
        min: [b.min[0] + delta[0], b.min[1] + delta[1], b.min[2] + delta[2]],
        max: [b.max[0] + delta[0], b.max[1] + delta[1], b.max[2] + delta[2]],
    }));
}

type KeyValueStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const defaultStorage = (): KeyValueStorage | undefined => {
    try {
        return globalThis.localStorage;
    } catch {
        return undefined;
    }
};

export function readStoredBoxes(scope: string, storage: KeyValueStorage | undefined = defaultStorage()): StoredBoxes {
    try {
        return parseStoredBoxes(storage?.getItem(bboxStorageKey(scope)) ?? null);
    } catch {
        return EMPTY_STORED_BOXES;
    }
}

/** Store `s` for `scope`; no boxes removes the entry. Storage failures are swallowed. */
export function writeStoredBoxes(
    scope: string,
    s: Omit<StoredBoxes, "v">,
    storage: KeyValueStorage | undefined = defaultStorage(),
): void {
    try {
        if (s.boxes.length === 0) storage?.removeItem(bboxStorageKey(scope));
        else storage?.setItem(bboxStorageKey(scope), serialiseStoredBoxes(s));
    } catch {
        // Private mode or a full store: the boxes live for this page only.
    }
}
