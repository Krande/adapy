// Images that face the camera, drawn over the model: what labels, markers and icons are made of. A
// feature describes them in plain data (a world position, pixels, a size) and the viewport on screen
// draws them, three.js as sprites in its scene and the three.js-free viewport as the renderer's own
// sprites. Nothing here (or anything it imports at runtime) may import three.js.

export type Point3 = readonly [number, number, number];

/** The pixels of a billboard. Billboards whose images have the same `key` share one texture, so `draw`
 *  runs once per key however many of them there are. */
export interface BillboardImage {
    key: string;
    /** Paint the image on a canvas of its own. */
    draw(): HTMLCanvasElement;
    /** Draw the pixels as three.js draws a texture that names no colour space: read as linear light,
     *  which comes out lighter than painted. The type icons have always looked so. Left out, the pixels
     *  are sRGB and come out as painted. */
    untagged?: boolean;
}

export interface Billboard {
    /** Where its anchor sits, in world space. */
    position: Point3;
    image: BillboardImage;
    /** Its height: in world units when `sizing` is "world" (it shrinks with distance), in CSS pixels
     *  when "screen" (the same size at any distance). The width follows from the image's shape. */
    height: number;
    sizing: "world" | "screen";
    /** The point of the image that sits on `position`, as fractions from its lower left corner. The
     *  centre, [0.5, 0.5], when left out. */
    anchor?: readonly [number, number];
    /** Billboards with a higher order are drawn over those with a lower one. */
    order?: number;
    /** Whatever the feature wants back when the billboard is clicked. */
    tag?: unknown;
}

/** One feature's billboards in a viewport. They are drawn over the model, never hidden behind it, and
 *  take no part in fitting the camera or in picking the model's objects. */
export interface BillboardLayer {
    /** Returns the handle `update` and `remove` take. */
    add(billboard: Billboard): number;
    update(handle: number, billboard: Billboard): void;
    remove(handle: number): void;
    clear(): void;
    /** Call `listener` for a click that lands on billboards of this layer, with their tags from the
     *  front one back. Returns the unsubscribe function. */
    onClick(listener: (tags: unknown[], event: MouseEvent) => void): () => void;
    /** Remove everything and release what the layer holds; the layer is not used again. */
    dispose(): void;
}

/** A perspective camera, as far as placing billboards on screen needs it. */
export interface BillboardCamera {
    eye: Point3;
    target: Point3;
    up: Point3;
    /** The vertical field of view, in degrees. */
    fovDeg: number;
}

const focal = (fovDeg: number): number => 1 / Math.tan((fovDeg * Math.PI) / 360);

/** The scale that draws a billboard `heightPx` high in a view `viewHeightPx` high, for a sprite that
 *  does not shrink with distance: such a sprite's scale is a fraction of the view's height, times the
 *  projection's focal term (as three.js sizes a Sprite without size attenuation). */
export function screenScale(heightPx: number, viewHeightPx: number, fovDeg: number): number {
    return (2 * heightPx * Math.tan((fovDeg * Math.PI) / 360)) / Math.max(1, viewHeightPx);
}

/** Where a world point is in a view of `width` by `height` (x right, y down, the view's own units), and
 *  how far in front of the camera; null when it is behind the camera. */
export function projectToView(camera: BillboardCamera, width: number, height: number, p: Point3): {x: number; y: number; depth: number} | null {
    const sub = (a: Point3, b: Point3): [number, number, number] => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    const dot = (a: Point3, b: Point3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a: Point3, b: Point3): [number, number, number] => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const unit = (a: Point3): [number, number, number] => {
        const l = Math.hypot(a[0], a[1], a[2]) || 1;
        return [a[0] / l, a[1] / l, a[2] / l];
    };
    const forward = unit(sub(camera.target, camera.eye));
    const right = unit(cross(forward, camera.up));
    const up = cross(right, forward);
    const rel = sub(p, camera.eye);
    const depth = dot(rel, forward);
    if (depth <= 1e-9) return null;
    // Half the view's height is one unit at distance `focal`.
    const perUnit = (focal(camera.fovDeg) * height) / (2 * depth);
    return {x: width / 2 + dot(rel, right) * perUnit, y: height / 2 - dot(rel, up) * perUnit, depth};
}

/** What `billboardsAt` needs of a billboard: `aspect` is its image's width over its height. */
export type PlacedBillboard = Pick<Billboard, "position" | "height" | "sizing" | "anchor" | "order" | "tag"> & {aspect: number};

/** The tags of the billboards under (x, y) in a view of `width` by `height` CSS pixels, the one drawn on
 *  top first: by order, then the nearer one. */
export function billboardsAt(
    camera: BillboardCamera, width: number, height: number, billboards: Iterable<PlacedBillboard>, x: number, y: number,
): unknown[] {
    const hits: {tag: unknown; order: number; depth: number}[] = [];
    for (const b of billboards) {
        const at = projectToView(camera, width, height, b.position);
        if (!at) continue;
        const h = b.sizing === "screen" ? b.height : (b.height * focal(camera.fovDeg) * height) / (2 * at.depth);
        const w = h * b.aspect;
        const [ax, ay] = b.anchor ?? [0.5, 0.5];
        const left = at.x - ax * w;
        const bottom = at.y + ay * h;
        if (x < left || x > left + w || y > bottom || y < bottom - h) continue;
        hits.push({tag: b.tag, order: b.order ?? 0, depth: at.depth});
    }
    return hits.sort((a, b) => b.order - a.order || a.depth - b.depth).map((hit) => hit.tag);
}
