/**
 * Whether a draw range is SHOWN -- the one truth every selection path asks before it takes an
 * element, so a hidden element is never selected (click, box, tree, group, search).
 *
 * Hidden means any of:
 *  - the range is in its batched mesh's hidden set (`CustomBatchedMesh.getHiddenRanges`), which is
 *    what Hide, Hide unselected, the tree's hide / isolate and the clash isolation all write --
 *    a hidden tree branch is its ranges in that set, and isolate is "every other range" in it;
 *  - the object, or an ancestor of it, is not `visible` (a model switched off whole);
 *  - for spatial selection only (box, window, crossing): the range lies wholly on the cut-away side
 *    of a section plane, so nothing of it is drawn.
 *
 * Duck-typed rather than `instanceof CustomBatchedMesh`, so it is testable without a renderer and
 * survives a hot reload that leaves meshes of an older copy of the class in the scene.
 */

interface ObjectLike {
    visible?: boolean;
    parent?: ObjectLike | null;
    traverse?: (cb: (o: ObjectLike) => void) => void;
}

interface BatchedLike extends ObjectLike {
    getHiddenRanges: () => ReadonlySet<string>;
    drawRanges?: ReadonlyMap<string, unknown>;
}

function isBatched(o: unknown): o is BatchedLike {
    return !!o && typeof (o as BatchedLike).getHiddenRanges === "function";
}

/** The object and every ancestor are `visible`. */
export function objectShown(o: ObjectLike): boolean {
    for (let cur: ObjectLike | null | undefined = o; cur; cur = cur.parent) {
        if (cur.visible === false) return false;
    }
    return true;
}

/**
 * Is range `id` of `obj` hidden? `obj` is what the selection is keyed by: a batched mesh, or a
 * wrapper whose subtree holds the batched mesh(es) that draw the range -- hidden there when every
 * batched mesh under it that owns the range hides it. Anything else (points, plain meshes) is
 * hidden only through `visible`.
 */
export function rangeHidden(obj: ObjectLike, id: string): boolean {
    return !objectShown(obj) || hiddenInside(obj, id);
}

/** `rangeHidden` without the `visible` walk up from `obj`. */
function hiddenInside(obj: ObjectLike, id: string): boolean {
    if (isBatched(obj)) return obj.getHiddenRanges().has(id);
    if (typeof obj.traverse !== "function") return false;
    let owners = 0;
    let hidden = 0;
    obj.traverse((child) => {
        if (child === obj || !isBatched(child)) return;
        if (child.drawRanges && !child.drawRanges.has(id)) return;
        owners++;
        if (child.getHiddenRanges().has(id) || !objectShown(child)) hidden++;
    });
    return owners > 0 && hidden === owners;
}

/** `batch` without the entries whose range is hidden. Returns `batch` itself when none is. */
export function withoutHidden<T extends ObjectLike>(batch: readonly (readonly [T, string])[]): (readonly [T, string])[] {
    const shown = new Map<T, boolean>();
    let dropped = false;
    const out: (readonly [T, string])[] = [];
    for (const entry of batch) {
        const [obj, id] = entry;
        let visible = shown.get(obj);
        if (visible === undefined) shown.set(obj, (visible = objectShown(obj)));
        if (!visible || hiddenInside(obj, id)) {
            dropped = true;
            continue;
        }
        out.push(entry);
    }
    return dropped ? out : (batch as (readonly [T, string])[]);
}

/** A section plane as three.js has it: kept where normal . p + constant >= 0. */
export interface PlaneLike {
    readonly normal: {readonly x: number; readonly y: number; readonly z: number};
    readonly constant: number;
}

/**
 * Is the world AABB at `o` in `b` (min x, y, z, max x, y, z) wholly cut away by one of `planes`?
 * three.js discards whatever is on the negative side of ANY plane (the union mode the section
 * planes use), so a box is gone when, for one plane, even its corner furthest along the normal is
 * behind it.
 */
export function boxCutAway(b: ArrayLike<number>, o: number, planes: readonly PlaneLike[] | null | undefined): boolean {
    if (!planes || planes.length === 0) return false;
    for (const {normal: n, constant} of planes) {
        const x = n.x >= 0 ? b[o + 3] : b[o];
        const y = n.y >= 0 ? b[o + 4] : b[o + 1];
        const z = n.z >= 0 ? b[o + 5] : b[o + 2];
        if (n.x * x + n.y * y + n.z * z + constant < 0) return true;
    }
    return false;
}

/** What a spatial selection may take from candidates `ids` with world boxes `bounds` (six numbers
 *  each, in the same order): the ones neither hidden nor wholly cut away. */
export function selectableInBounds(
    ids: readonly string[],
    bounds: ArrayLike<number>,
    hidden: ReadonlySet<string>,
    planes: readonly PlaneLike[] | null | undefined,
): string[] {
    const out: string[] = [];
    for (let r = 0; r < ids.length; r++) {
        if (hidden.has(ids[r]) || boxCutAway(bounds, r * 6, planes)) continue;
        out.push(ids[r]);
    }
    return out;
}
