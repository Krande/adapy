// Images lying in the world (viewport/api/imageLayer.ts) on the three.js viewport: one quad per image,
// textured with a DataTexture whose pixels are written in place. The adacpp WebGPU viewport draws what
// its scene mirror makes of the mesh.
import * as THREE from "three";

import {requestRender} from "@/state/perfStore";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {rectCorners, validImage, type ImageLayer, type ImageRect, type WorldImage} from "./imageLayer";

interface Item {
    mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    texture: THREE.DataTexture;
    nearest: boolean;
    onTop: boolean;
}

/** The image's bytes into `to`: one copy when the caller's array is exactly the image. */
function copyPixels(to: Uint8Array, from: ArrayLike<number>): void {
    if (from.length === to.length) to.set(from);
    else for (let i = 0; i < to.length; i++) to[i] = from[i];
}

function makeTexture(pixels: ArrayLike<number>, width: number, height: number, nearest: boolean): THREE.DataTexture {
    // A copy: the texture keeps the array it is given, and the caller keeps its own.
    const data = new Uint8Array(width * height * 4);
    copyPixels(data, pixels);
    const texture = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.UnsignedByteType);
    // The bytes are sRGB: the GPU decodes them when it samples, so blending between pixels is linear.
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.magFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
    // No mipmaps: they would be made again for every new set of pixels.
    texture.minFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
    texture.generateMipmaps = false;
    texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
    texture.needsUpdate = true;
    return texture;
}

function place(geometry: THREE.BufferGeometry, rect: ImageRect): void {
    const attr = geometry.getAttribute("position") as THREE.BufferAttribute;
    rectCorners(rect, attr.array as Float32Array);
    attr.needsUpdate = true;
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
}

/** An image layer whose meshes are added to `parent()` (asked at each add: the scene on screen). */
export function createThreeImageLayer(parent: () => THREE.Object3D | null, name: string): ImageLayer {
    const items = new Map<number, Item>();
    let nextHandle = 1;

    const drop = (handle: number): void => {
        const item = items.get(handle);
        if (!item) return;
        items.delete(handle);
        item.mesh.removeFromParent();
        item.mesh.geometry.dispose();
        item.mesh.material.dispose();
        item.texture.dispose();
    };

    return {
        add: (image: WorldImage) => {
            const ok = validImage(image.pixels, image.width, image.height);
            // An image without usable pixels is one clear pixel until it is given some.
            const texture = ok ? makeTexture(image.pixels, image.width, image.height, !!image.nearest) : makeTexture([0, 0, 0, 0], 1, 1, !!image.nearest);
            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(12), 3));
            // The first row of pixels is the texture's row 0, which lies at v = 0: along `u` at the origin.
            geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]), 2));
            geometry.setIndex([0, 1, 2, 0, 2, 3]);
            place(geometry, image);
            const material = new THREE.MeshBasicMaterial({
                map: texture,
                side: THREE.DoubleSide,
                // Always blended: a pixel's own alpha counts whatever the image's opacity.
                transparent: true,
                opacity: Math.max(0, Math.min(1, image.opacity ?? 1)),
                depthTest: !image.onTop,
                depthWrite: false,
                // Its colours are data (a cell's state), not a lit surface's.
                toneMapped: false,
                // In front of a floor it lies on, not flickering in it.
                polygonOffset: true,
                polygonOffsetFactor: -1,
                polygonOffsetUnits: -1,
            });
            const mesh = new THREE.Mesh(geometry, material);
            mesh.name = name;
            // Under the lines and points drawn on it (render order 7), over the model.
            mesh.renderOrder = image.onTop ? 19 : 6;
            if (image.onTop) mesh.userData.__overlayPass = true;
            mesh.visible = image.visible !== false;
            mesh.raycast = () => undefined; // a click goes to the model under it
            // The WebGPU viewport that mirrors the scene draws it as a textured quad of the renderer.
            mesh.userData.__worldImage = true;
            if (image.matrix) {
                mesh.matrixAutoUpdate = false;
                mesh.matrix.fromArray(image.matrix);
            }
            parent()?.add(mesh);
            if (image.clipped) clipWithModel(mesh);
            const handle = nextHandle++;
            items.set(handle, {mesh, texture, nearest: !!image.nearest, onTop: !!image.onTop});
            requestRender();
            return handle;
        },
        setPixels: (handle, pixels, width, height) => {
            const item = items.get(handle);
            if (!item) return;
            const held = item.texture.image as {data: Uint8Array; width: number; height: number};
            const w = width ?? held.width, h = height ?? held.height;
            if (!validImage(pixels, w, h)) return;
            if (w === held.width && h === held.height) {
                // In place: the texture keeps its storage on the GPU, and its version says it changed.
                copyPixels(held.data, pixels);
                item.texture.needsUpdate = true;
            } else {
                // Another size is another texture: a texture's storage cannot be resized.
                item.texture.dispose();
                item.texture = makeTexture(pixels, w, h, item.nearest);
                item.mesh.material.map = item.texture;
            }
            requestRender();
        },
        setRect: (handle, rect) => {
            const item = items.get(handle);
            if (!item) return;
            place(item.mesh.geometry, rect);
            requestRender();
        },
        setLook: (handle, look) => {
            const item = items.get(handle);
            if (!item) return;
            if (look.visible !== undefined) item.mesh.visible = look.visible;
            if (look.opacity !== undefined) item.mesh.material.opacity = Math.max(0, Math.min(1, look.opacity));
            if (look.matrix !== undefined) {
                item.mesh.matrixAutoUpdate = false;
                if (look.matrix) item.mesh.matrix.fromArray(look.matrix);
                else item.mesh.matrix.identity();
                item.mesh.matrixWorldNeedsUpdate = true;
            }
            requestRender();
        },
        // A texture as large as the GPU takes; nothing a map of cells comes near.
        unavailable: () => null,
        remove: (handle) => {
            drop(handle);
            requestRender();
        },
        clear: () => {
            for (const handle of [...items.keys()]) drop(handle);
            requestRender();
        },
        dispose: () => {
            for (const handle of [...items.keys()]) drop(handle);
            requestRender();
        },
    };
}
