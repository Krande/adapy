// A three.js scene of a tool's own, drawn through the viewport interface (viewport/api): its meshes and
// lines as the viewport's overlays (./api/overlayLayer.ts), its sprites as billboards
// (./api/billboards.ts). For a viewport with no three.js scene to put them in, the three.js-free one:
// the cell builder keeps its cells, gizmos and markers in such a scene there and is drawn by this
// (components/viewer/cellbuilder/nativeViewHost.ts). The three.js viewport draws such a scene itself.
//
// What is mirrored, as three.js draws it:
//  - a Mesh: one overlay mesh per material group, in the material's colour and opacity; a material
//    that is lit (not a MeshBasicMaterial) has a fixed light baked into its colours, as overlays are
//    unlit. `depthTest: false` draws it over the model;
//  - LineSegments, Line, LineLoop and fat lines (LineSegments2): overlay lines, dashed as their
//    material says, their own colours per vertex when they have them;
//  - a Sprite: a billboard of its texture, at one size on screen (`sizeAttenuation: false`) or in
//    the world, anchored at its centre.
// A material's clipping planes make it cut by the section planes; nothing else is. Geometry is sent
// once per buffer version; a move, a colour, an opacity or a hide is a change of look.
import * as THREE from "three";
import type {Billboard, BillboardLayer} from "@/viewport/api/billboards";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";

export interface MirrorTarget {
    overlays: OverlayLayer;
    billboards: BillboardLayer;
    /** The view's height in CSS pixels and the camera's vertical field of view: what a sprite drawn
     *  at one size on screen is sized by. */
    screen(): {height: number; fovDeg: number};
    /** The overlays draw a lit mesh (OverlayMesh.lit) with the model's lights: a lit material is then
     *  mirrored lit, with its normals, instead of with a light baked into its colours. */
    litOverlays?: boolean;
}

export interface SceneMirror {
    /** Bring what the viewport draws up to date with the scene. Cheap when nothing changed. */
    sync(): void;
    /** Take everything off the viewport. */
    dispose(): void;
}

type Rgb = [number, number, number];

interface Part {
    handle: number;
    /** Its material, by index into the object's materials. */
    material: number;
    color: Rgb;
    opacity: number;
    visible: boolean;
}

interface Entry {
    signature: string;
    parts: Part[];
    matrix: Float64Array;
    sprite: {handle: number; billboard: Billboard} | null;
    seen: number;
}

/** The light baked into a lit material's colours: from above and a little to one side. */
const LIGHT = new THREE.Vector3(0.35, 0.45, 0.82).normalize();
const AMBIENT = 0.45;

type AnyMaterial = THREE.Material & {
    color?: THREE.Color;
    vertexColors?: boolean;
    map?: THREE.Texture | null;
    linewidth?: number;
    dashed?: boolean;
    dashSize?: number;
    gapSize?: number;
    dashScale?: number;
    scale?: number;
    sizeAttenuation?: boolean;
};

const flag = (o: unknown, name: string): boolean => !!(o as Record<string, unknown> | null)?.[name];

const materialsOf = (obj: THREE.Object3D): AnyMaterial[] => {
    const m = (obj as THREE.Mesh).material as AnyMaterial | AnyMaterial[] | undefined;
    return Array.isArray(m) ? m : m ? [m] : [];
};

const rgbOf = (m: AnyMaterial | undefined): Rgb => (m?.color instanceof THREE.Color ? [m.color.r, m.color.g, m.color.b] : [1, 1, 1]);
const opacityOf = (m: AnyMaterial | undefined): number => (m && m.transparent ? Math.max(0, Math.min(1, m.opacity)) : 1);
const sameRgb = (a: Rgb, b: Rgb): boolean => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
const isLit = (m: AnyMaterial | undefined): boolean =>
    !!m && !flag(m, "isMeshBasicMaterial") && (flag(m, "isMeshStandardMaterial") || flag(m, "isMeshPhongMaterial") || flag(m, "isMeshLambertMaterial") || flag(m, "isMeshPhysicalMaterial"));

/** Dash and gap in the units of the coordinates; null draws solid. */
function dashesOf(m: AnyMaterial | undefined): {dash: number; gap: number} | null {
    if (!m || !(flag(m, "isLineDashedMaterial") || (flag(m, "isLineMaterial") && m.dashed))) return null;
    const scale = (flag(m, "isLineMaterial") ? m.dashScale : m.scale) || 1;
    return {dash: (m.dashSize ?? 0) / scale, gap: (m.gapSize ?? 0) / scale};
}

function attrVersion(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute | null | undefined): number {
    if (!a) return -1;
    return (a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute ? (a as THREE.InterleavedBufferAttribute).data.version : (a as THREE.BufferAttribute).version;
}

function floats(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute): Float32Array {
    const out = new Float32Array(a.count * 3);
    for (let i = 0; i < a.count; i++) {
        out[i * 3] = a.getX(i);
        out[i * 3 + 1] = a.getY(i);
        out[i * 3 + 2] = a.getZ(i);
    }
    return out;
}

/** Six numbers per segment: what a line object draws, as three.js joins its vertices. */
export function lineSegmentsOf(obj: THREE.Object3D): Float32Array | null {
    const geometry = (obj as THREE.Line).geometry as THREE.BufferGeometry | undefined;
    if (!geometry) return null;
    if (flag(obj, "isLineSegments2")) {
        const start = geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute | undefined;
        const end = geometry.getAttribute("instanceEnd") as THREE.InterleavedBufferAttribute | undefined;
        if (!start || !end) return null;
        const n = (geometry as THREE.InstancedBufferGeometry).instanceCount;
        const count = Math.min(start.count, Number.isFinite(n) && n >= 0 ? n : start.count);
        const out = new Float32Array(count * 6);
        for (let i = 0; i < count; i++) {
            out.set([start.getX(i), start.getY(i), start.getZ(i), end.getX(i), end.getY(i), end.getZ(i)], i * 6);
        }
        return out;
    }
    const position = geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!position) return null;
    const index = geometry.index;
    const order: number[] = [];
    const n = index ? index.count : position.count;
    for (let i = 0; i < n; i++) order.push(index ? index.getX(i) : i);
    const pairs: number[] = [];
    if (flag(obj, "isLineSegments")) {
        for (let i = 0; i + 1 < order.length; i += 2) pairs.push(order[i], order[i + 1]);
    } else {
        for (let i = 0; i + 1 < order.length; i++) pairs.push(order[i], order[i + 1]);
        if (flag(obj, "isLineLoop") && order.length > 2) pairs.push(order[order.length - 1], order[0]);
    }
    const out = new Float32Array(pairs.length * 3);
    pairs.forEach((v, k) => {
        out[k * 3] = position.getX(v);
        out[k * 3 + 1] = position.getY(v);
        out[k * 3 + 2] = position.getZ(v);
    });
    return out;
}

/** A colour per segment (that of its first vertex) of a line object with vertex colours; null without. */
function segmentColorsOf(obj: THREE.Object3D): Float32Array | null {
    const geometry = (obj as THREE.Line).geometry as THREE.BufferGeometry;
    const color = geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
    if (!color || !flag(obj, "isLineSegments") || geometry.index) return null;
    const segments = Math.floor(color.count / 2);
    const out = new Float32Array(segments * 3);
    for (let s = 0; s < segments; s++) {
        out[s * 3] = color.getX(s * 2);
        out[s * 3 + 1] = color.getY(s * 2);
        out[s * 3 + 2] = color.getZ(s * 2);
    }
    return out;
}

/** Each material group of a mesh: the triangles it draws (indices into the positions) and its
 *  material. As three.js draws it: by its groups only with an array of materials (a group whose
 *  material is missing draws nothing), all of it with one material. */
export function meshParts(geometry: THREE.BufferGeometry, materials: readonly unknown[], grouped = materials.length > 1): {indices: Uint32Array; material: number}[] {
    const index = geometry.index;
    const position = geometry.getAttribute("position");
    const total = index ? index.count : (position?.count ?? 0);
    const groups = grouped && geometry.groups.length ? geometry.groups : [{start: 0, count: total, materialIndex: 0}];
    const out: {indices: Uint32Array; material: number}[] = [];
    for (const g of groups) {
        const material = g.materialIndex ?? 0;
        if (material >= materials.length) continue;
        const start = Math.max(0, g.start);
        const end = Math.min(total, start + (Number.isFinite(g.count) ? g.count : total));
        const count = Math.max(0, end - start) - (Math.max(0, end - start) % 3);
        if (!count) continue;
        const indices = new Uint32Array(count);
        for (let i = 0; i < count; i++) indices[i] = index ? index.getX(start + i) : start + i;
        out.push({indices, material});
    }
    return out;
}

/** The vertex colours of a lit mesh under the baked light, in its world frame. */
function bakedColors(geometry: THREE.BufferGeometry, base: Rgb, world: THREE.Matrix4): Float32Array {
    const position = geometry.getAttribute("position");
    const n = position.count;
    const out = new Float32Array(n * 3);
    let normal = geometry.getAttribute("normal");
    if (!normal) {
        geometry.computeVertexNormals();
        normal = geometry.getAttribute("normal");
    }
    const normalMatrix = new THREE.Matrix3().getNormalMatrix(world);
    const v = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
        let shade = 1;
        if (normal) {
            v.set(normal.getX(i), normal.getY(i), normal.getZ(i)).applyMatrix3(normalMatrix).normalize();
            // Both sides lit, as the overlay draws both.
            shade = AMBIENT + (1 - AMBIENT) * Math.abs(v.dot(LIGHT));
        }
        out[i * 3] = base[0] * shade;
        out[i * 3 + 1] = base[1] * shade;
        out[i * 3 + 2] = base[2] * shade;
    }
    return out;
}

/** The height a sprite is drawn at: in CSS pixels for one of no size attenuation (as three.js'
 *  sprite shader sizes it in perspective: its scale is a share of the view's height times the
 *  projection's focal term), in world units otherwise. */
export function spriteHeight(scaleY: number, sizeAttenuation: boolean, viewHeightPx: number, fovDeg: number): number {
    if (sizeAttenuation) return scaleY;
    return (scaleY * viewHeightPx) / (2 * Math.tan((fovDeg * Math.PI) / 360));
}

let scratch: HTMLCanvasElement | null = null;
/** The texture's picture on a canvas: a sprite's map is a canvas, an image or a bitmap. */
function canvasOf(image: unknown): HTMLCanvasElement {
    if (typeof HTMLCanvasElement !== "undefined" && image instanceof HTMLCanvasElement) return image;
    const src = image as (CanvasImageSource & {width: number; height: number}) | null;
    scratch ??= document.createElement("canvas");
    scratch.width = src?.width || 1;
    scratch.height = src?.height || 1;
    const g = scratch.getContext("2d");
    if (g && src) {
        g.clearRect(0, 0, scratch.width, scratch.height);
        g.drawImage(src, 0, 0);
    }
    return scratch;
}

// The parts of WebGLRenderer an object's onBeforeRender hook may ask for.
const hookRenderer = {
    getSize: (target: THREE.Vector2) => target.set(1, 1),
    getDrawingBufferSize: (target: THREE.Vector2) => target.set(1, 1),
    getPixelRatio: () => 1,
};
const noHook = THREE.Object3D.prototype.onBeforeRender;

export function createSceneMirror(scene: THREE.Scene, camera: THREE.Camera, target: MirrorTarget): SceneMirror {
    const entries = new Map<THREE.Object3D, Entry>();
    let frame = 0;
    const position = new THREE.Vector3();
    const scale = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();

    const drop = (entry: Entry): void => {
        for (const part of entry.parts) target.overlays.remove(part.handle);
        entry.parts = [];
        if (entry.sprite) target.billboards.remove(entry.sprite.handle);
        entry.sprite = null;
    };

    const sameMatrix = (a: Float64Array, b: ArrayLike<number>): boolean => {
        for (let i = 0; i < 16; i++) if (a[i] !== b[i]) return false;
        return true;
    };

    /** A part's look as it is now, changed on the viewport where it differs. */
    const restyle = (part: Part, color: Rgb, opacity: number, visible: boolean): void => {
        if (!sameRgb(part.color, color)) {
            part.color = color;
            target.overlays.setLook(part.handle, {color});
        }
        if (part.opacity !== opacity) {
            part.opacity = opacity;
            target.overlays.setLook(part.handle, {opacity});
        }
        if (part.visible !== visible) {
            part.visible = visible;
            target.overlays.setLook(part.handle, {visible});
        }
    };

    const meshSignature = (mesh: THREE.Mesh, materials: AnyMaterial[]): string => {
        const g = mesh.geometry as THREE.BufferGeometry;
        const groups = g.groups.map((x) => `${x.start},${x.count},${x.materialIndex}`).join(";");
        const looks = materials.map((m) => `${m.depthTest ? 1 : 0}${m.clippingPlanes?.length ? 1 : 0}${isLit(m) ? 1 : 0}${m.side}`).join("");
        return `m|${g.uuid}|${attrVersion(g.getAttribute("position"))}|${attrVersion(g.index)}|${groups}|${looks}|${mesh.renderOrder}`;
    };

    const syncMesh = (mesh: THREE.Mesh, visible: boolean, entry: Entry | undefined): Entry | null => {
        const materials = materialsOf(mesh);
        const geometry = mesh.geometry as THREE.BufferGeometry | undefined;
        if (!geometry?.getAttribute("position") || !materials.length) return null;
        const signature = meshSignature(mesh, materials);
        const matrix = mesh.matrixWorld.elements;
        if (!entry || entry.signature !== signature) {
            if (entry) drop(entry);
            if (!visible) return entry ?? null;
            const positions = floats(geometry.getAttribute("position"));
            const parts: Part[] = [];
            // A lit material is drawn lit where the viewport can (its normals, in the mesh's own axes as
            // the positions are), with a fixed light baked into its colours where it cannot.
            const litOverlays = target.litOverlays ?? false;
            let normals: Float32Array | undefined;
            for (const {indices, material} of meshParts(geometry, materials, Array.isArray(mesh.material))) {
                const m = materials[material];
                const color = rgbOf(m);
                const opacity = opacityOf(m);
                const shown = visible && m.visible;
                const lit = isLit(m);
                if (lit && litOverlays && !normals) {
                    if (!geometry.getAttribute("normal")) geometry.computeVertexNormals();
                    const n = geometry.getAttribute("normal");
                    normals = n ? floats(n) : undefined;
                }
                const standard = m as AnyMaterial & {metalness?: number; roughness?: number};
                const handle = target.overlays.addMesh({
                    positions,
                    indices,
                    color,
                    colors: lit && !litOverlays ? bakedColors(geometry, color, mesh.matrixWorld) : undefined,
                    lit: lit && litOverlays ? {metalness: standard.metalness ?? 0, roughness: standard.roughness ?? 1} : undefined,
                    normals: lit && litOverlays ? normals : undefined,
                    opacity,
                    onTop: m.depthTest === false,
                    clipped: !!m.clippingPlanes?.length,
                    frontOnly: m.side === THREE.FrontSide,
                    renderOrder: mesh.renderOrder,
                    matrix,
                    fitted: false,
                });
                if (!shown) target.overlays.setLook(handle, {visible: false});
                parts.push({handle, material, color, opacity, visible: shown});
            }
            const made: Entry = {signature, parts, matrix: Float64Array.from(matrix), sprite: null, seen: frame};
            return made;
        }
        for (const part of entry.parts) {
            const m = materials[part.material] ?? materials[0];
            restyle(part, rgbOf(m), opacityOf(m), visible && m.visible);
        }
        if (!sameMatrix(entry.matrix, matrix)) {
            entry.matrix.set(matrix);
            for (const part of entry.parts) target.overlays.setLook(part.handle, {matrix});
        }
        return entry;
    };

    const syncLines = (line: THREE.Object3D, visible: boolean, entry: Entry | undefined): Entry | null => {
        const m = materialsOf(line)[0];
        const geometry = (line as THREE.Line).geometry as THREE.BufferGeometry | undefined;
        if (!m || !geometry) return null;
        const fat = flag(line, "isLineSegments2");
        const source = fat ? geometry.getAttribute("instanceStart") : geometry.getAttribute("position");
        const dashes = dashesOf(m);
        const signature = `l|${geometry.uuid}|${attrVersion(source)}|${attrVersion(geometry.index)}|${attrVersion(geometry.getAttribute("color"))}|` +
            `${m.depthTest ? 1 : 0}${m.clippingPlanes?.length ? 1 : 0}|${dashes ? `${dashes.dash},${dashes.gap}` : ""}|${fat ? m.linewidth : 1}|${line.renderOrder}`;
        const matrix = line.matrixWorld.elements;
        const color = rgbOf(m);
        const opacity = opacityOf(m);
        const shown = visible && m.visible;
        if (!entry || entry.signature !== signature) {
            if (entry) drop(entry);
            if (!visible) return entry ?? null;
            const segments = lineSegmentsOf(line);
            if (!segments || !segments.length) return {signature, parts: [], matrix: Float64Array.from(matrix), sprite: null, seen: frame};
            const colors = m.vertexColors ? segmentColorsOf(line) : null;
            const handle = target.overlays.addLines({
                segments,
                colors: colors ?? undefined,
                color,
                opacity,
                widthPx: fat ? (m.linewidth ?? 1) : 1,
                dashed: dashes ?? undefined,
                onTop: m.depthTest === false,
                clipped: !!m.clippingPlanes?.length,
                renderOrder: line.renderOrder,
                matrix,
                fitted: false,
            });
            if (!shown) target.overlays.setLook(handle, {visible: false});
            return {signature, parts: [{handle, material: 0, color, opacity, visible: shown}], matrix: Float64Array.from(matrix), sprite: null, seen: frame};
        }
        for (const part of entry.parts) restyle(part, color, opacity, shown);
        if (!sameMatrix(entry.matrix, matrix)) {
            entry.matrix.set(matrix);
            for (const part of entry.parts) target.overlays.setLook(part.handle, {matrix});
        }
        return entry;
    };

    const syncSprite = (sprite: THREE.Sprite, visible: boolean, entry: Entry | undefined): Entry | null => {
        const m = sprite.material as AnyMaterial;
        const map = m.map;
        const shown = visible && m.visible && !!map?.image;
        if (!shown || !map) {
            if (entry) drop(entry);
            return entry ?? null;
        }
        sprite.matrixWorld.decompose(position, quaternion, scale);
        const screen = target.screen();
        const billboard: Billboard = {
            position: [position.x, position.y, position.z],
            image: {key: `${map.uuid}:${map.version}`, draw: () => canvasOf(map.image), untagged: map.colorSpace !== THREE.SRGBColorSpace},
            height: spriteHeight(scale.y, m.sizeAttenuation !== false, screen.height, screen.fovDeg),
            sizing: m.sizeAttenuation === false ? "screen" : "world",
            anchor: [sprite.center.x, sprite.center.y],
            order: 9995 + sprite.renderOrder,
        };
        const held = entry?.sprite;
        if (!held) {
            const made: Entry = entry ?? {signature: "s", parts: [], matrix: new Float64Array(16), sprite: null, seen: frame};
            made.sprite = {handle: target.billboards.add(billboard), billboard};
            return made;
        }
        const was = held.billboard;
        const moved =
            was.image.key !== billboard.image.key ||
            Math.abs(was.height - billboard.height) > 1e-6 ||
            was.position.some((v, i) => v !== billboard.position[i]) ||
            was.anchor?.[0] !== billboard.anchor?.[0] ||
            was.anchor?.[1] !== billboard.anchor?.[1] ||
            was.order !== billboard.order ||
            was.sizing !== billboard.sizing;
        if (moved) {
            target.billboards.update(held.handle, billboard);
            held.billboard = billboard;
        }
        return entry!;
    };

    const visit = (obj: THREE.Object3D, parentVisible: boolean): void => {
        const visible = parentVisible && obj.visible;
        const before = entries.get(obj);
        let entry: Entry | null = null;
        if (flag(obj, "isSprite")) {
            entry = syncSprite(obj as THREE.Sprite, visible, before);
        } else if (flag(obj, "isLineSegments2") || flag(obj, "isLine")) {
            if (visible && obj.onBeforeRender !== noHook && !flag(obj, "isLineSegments2")) runHook(obj);
            entry = syncLines(obj, visible, before);
        } else if (flag(obj, "isMesh")) {
            if (visible && obj.onBeforeRender !== noHook) runHook(obj);
            entry = syncMesh(obj as THREE.Mesh, visible, before);
        }
        if (entry) {
            entry.seen = frame;
            entries.set(obj, entry);
        }
        for (const child of obj.children) visit(child, visible);
    };

    /** three.js calls an object's onBeforeRender as it draws it; nothing draws this scene, so it is
     *  called here (a dimension line follows the camera round its box from there). */
    const runHook = (obj: THREE.Object3D): void => {
        const mesh = obj as THREE.Mesh;
        try {
            obj.onBeforeRender(hookRenderer as unknown as THREE.WebGLRenderer, scene, camera, mesh.geometry, materialsOf(obj)[0] as THREE.Material, null as unknown as THREE.Group);
        } catch (err) {
            console.warn("[scene mirror] an onBeforeRender hook failed:", err);
        }
    };

    return {
        sync: () => {
            frame++;
            scene.updateMatrixWorld();
            visit(scene, true);
            for (const [obj, entry] of entries) {
                if (entry.seen === frame) continue;
                drop(entry);
                entries.delete(obj);
            }
        },
        dispose: () => {
            for (const entry of entries.values()) drop(entry);
            entries.clear();
        },
    };
}
