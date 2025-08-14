import * as THREE from "three";
import {cameraRef, rendererRef, sceneRef} from "../../state/refs";

// Simple singleton GPU point picker for THREE.Points
// - Assigns each point (vertex) a unique RGB id (1..16,777,215)
// - Renders only points into an offscreen target with a picking shader that matches impostor sizing
// - Reads the pixel under the mouse to resolve {object, index}
// - Computes world position with morphs on CPU for stable reporting

export type GpuPickResult = {
    object: THREE.Points;
    index: number;
    worldPosition: THREE.Vector3;
} | null;

class GpuPointPicker {
    private rt: THREE.WebGLRenderTarget | null = null;
    private size = new THREE.Vector2(1, 1);
    private idCounter = 1; // 0 reserved for background

    // Map globalId -> { obj, index }
    private idToEntry = new Map<number, { obj: THREE.Points; index: number }>();
    // Map Points -> picking material (cached)
    private pickMats = new WeakMap<THREE.Points, THREE.ShaderMaterial>();

    // Set up or resize render target
    private ensureRT(renderer: THREE.WebGLRenderer) {
        renderer.getSize(this.size);
        const w = Math.max(1, Math.floor(this.size.x));
        const h = Math.max(1, Math.floor(this.size.y));
        if (!this.rt || this.rt.width !== w || this.rt.height !== h) {
            if (this.rt) this.rt.dispose();
            this.rt = new THREE.WebGLRenderTarget(w, h, {
                depthBuffer: true,
                stencilBuffer: false,
                type: THREE.UnsignedByteType,
                format: THREE.RGBAFormat,
            });
        }
    }

    // Create or get a picking material mirroring impostor sizing and morphs
    private getPickingMaterial(points: THREE.Points): THREE.ShaderMaterial {
        let mat = this.pickMats.get(points);
        if (mat) return mat;

        const vertex = `
            precision mediump float;
            #include <common>
            #include <morphtarget_pars_vertex>
            attribute vec3 pickColor; // 0..1 encoded color id
            varying vec3 vPick;
            uniform float pointSize; // pixels
            uniform bool uWorldSize;
            uniform float uWorldPointSize; // world diameter
            uniform float uFov; // degrees
            uniform float uViewportHeight; // pixels
            void main() {
                vPick = pickColor;
                vec3 transformed = position;
                #include <morphtarget_vertex>
                vec4 mvPosition = modelViewMatrix * vec4(transformed, 1.0);
                gl_Position = projectionMatrix * mvPosition;
                float pixelSize = pointSize;
                if (uWorldSize) {
                    float scale = uViewportHeight / (2.0 * tan(radians(uFov) * 0.5));
                    pixelSize = uWorldPointSize * scale / -mvPosition.z;
                }
                gl_PointSize = pixelSize;
            }
        `;
        const fragment = `
            precision mediump float;
            varying vec3 vPick;
            void main() {
                vec2 p = gl_PointCoord * 2.0 - 1.0;
                float r2 = dot(p, p);
                if (r2 > 1.0) discard; // circular mask to match impostor
                gl_FragColor = vec4(vPick, 1.0);
            }
        `;

        const sm = new THREE.ShaderMaterial({
            vertexShader: vertex,
            fragmentShader: fragment,
            uniforms: {
                pointSize: { value: 5.0 },
                uWorldSize: { value: false },
                uWorldPointSize: { value: 1.0 },
                uFov: { value: 50.0 },
                uViewportHeight: { value: 800.0 },
            },
            depthTest: true,
            depthWrite: true,
        });

        // Enable morph if geometry has morphs
        const g = points.geometry as THREE.BufferGeometry;
        const hasMorphs = !!g.morphAttributes && Array.isArray(g.morphAttributes.position) && g.morphAttributes.position.length > 0;
        if (hasMorphs) (sm as any).morphTargets = true;

        this.pickMats.set(points, sm);
        return sm;
    }

    // Assign unique ids per vertex for this Points object
    registerPoints(points: THREE.Points) {
        const geom = points.geometry as THREE.BufferGeometry;
        const count = (geom.getAttribute('position') as THREE.BufferAttribute).count;

        // If already has pickColor attribute with correct count, keep it
        const existing = geom.getAttribute('pickColor') as THREE.BufferAttribute | undefined;
        if (existing && existing.count === count) return; // assume mapped

        const colors = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
            const id = this.idCounter++;
            this.idToEntry.set(id, { obj: points, index: i });
            const r = (id & 0xFF) / 255.0;
            const g = ((id >> 8) & 0xFF) / 255.0;
            const b = ((id >> 16) & 0xFF) / 255.0;
            const o = i * 3;
            colors[o] = r; colors[o + 1] = g; colors[o + 2] = b;
        }
        const attr = new THREE.BufferAttribute(colors, 3, false);
        geom.setAttribute('pickColor', attr);
    }

    // Compute deformed world position for a given object/index using CPU morph blending
    private computeWorldPosition(points: THREE.Points, index: number): THREE.Vector3 {
        const geom = points.geometry as THREE.BufferGeometry;
        const pos = geom.getAttribute('position') as THREE.BufferAttribute;
        const v = new THREE.Vector3(pos.getX(index), pos.getY(index), pos.getZ(index));
        const morphs = (geom.morphAttributes && geom.morphAttributes.position) as THREE.BufferAttribute[] | undefined;
        const rel = geom.morphTargetsRelative === true;
        const influences: number[] | undefined = (points as any).morphTargetInfluences;
        if (morphs && influences && morphs.length === influences.length) {
            let sum = 0;
            for (let i = 0; i < morphs.length; i++) {
                const inf = influences[i] || 0;
                if (inf === 0) continue;
                sum += inf;
                const mp = morphs[i];
                const mx = mp.getX(index), my = mp.getY(index), mz = mp.getZ(index);
                if (rel) {
                    v.x += mx * inf; v.y += my * inf; v.z += mz * inf;
                } else {
                    v.x = v.x * (1 - sum) + mx * inf;
                    v.y = v.y * (1 - sum) + my * inf;
                    v.z = v.z * (1 - sum) + mz * inf;
                }
            }
        }
        return v.applyMatrix4(points.matrixWorld);
    }

    // Perform a GPU pick at client coordinates
    pickAt(clientX: number, clientY: number): GpuPickResult {
        const renderer = rendererRef.current;
        const scene = sceneRef.current;
        const camera = cameraRef.current as THREE.PerspectiveCamera | null;
        if (!renderer || !scene || !camera) return null;

        this.ensureRT(renderer);

        // Update picking materials uniforms to match sizing
        const fov = camera.fov;
        const height = this.rt!.height;
        // We need to know whether points are using world size; infer from impostor shader uniform if present, else default to screen pixels
        const updateMatUniforms = (pts: THREE.Points) => {
            const pickMat = this.getPickingMaterial(pts);
            // Try to mirror from the visual material if it exposes uniforms
            const mat = pts.material as any;
            if (mat && mat.uniforms) {
                if (mat.uniforms.pointSize) pickMat.uniforms.pointSize.value = mat.uniforms.pointSize.value;
                if (mat.uniforms.uWorldSize) pickMat.uniforms.uWorldSize.value = !!mat.uniforms.uWorldSize.value;
                if (mat.uniforms.uWorldPointSize) pickMat.uniforms.uWorldPointSize.value = mat.uniforms.uWorldPointSize.value;
            } else if ((mat as any).isPointsMaterial) {
                pickMat.uniforms.pointSize.value = (mat as THREE.PointsMaterial).size;
                pickMat.uniforms.uWorldSize.value = false;
            }
            pickMat.uniforms.uFov.value = fov;
            pickMat.uniforms.uViewportHeight.value = height;
        };

        // Prepare scene: swap materials for all Points that are registered
        const toSwap: Array<{ obj: THREE.Points; orig: THREE.Material | THREE.Material[] } > = [];
        scene.traverse(o => {
            if (o instanceof THREE.Points) {
                const g = o.geometry as THREE.BufferGeometry;
                if (!g.getAttribute('pickColor')) return; // not registered
                updateMatUniforms(o);
                toSwap.push({ obj: o, orig: o.material });
                o.material = this.getPickingMaterial(o);
            }
        });

        const prevTarget = renderer.getRenderTarget();
        const prevClear = renderer.getClearColor(new THREE.Color());
        const prevAlpha = renderer.getClearAlpha();

        renderer.setRenderTarget(this.rt);
        renderer.setClearColor(0x000000, 0);
        renderer.clear();
        renderer.render(scene, camera);

        const canvas = renderer.domElement;
        const rect = canvas.getBoundingClientRect();
        const x = Math.floor((clientX - rect.left) * (this.rt!.width / rect.width));
        const y = Math.floor((rect.bottom - clientY) * (this.rt!.height / rect.height));

        const pixel = new Uint8Array(4);
        renderer.readRenderTargetPixels(this.rt!, x, y, 1, 1, pixel);

        // Restore scene
        for (const {obj, orig} of toSwap) obj.material = orig;
        renderer.setRenderTarget(prevTarget);
        renderer.setClearColor(prevClear, prevAlpha);

        const id = pixel[0] + (pixel[1] << 8) + (pixel[2] << 16);
        if (id === 0) return null;
        const entry = this.idToEntry.get(id);
        if (!entry) return null;

        const worldPosition = this.computeWorldPosition(entry.obj, entry.index);
        return { object: entry.obj, index: entry.index, worldPosition };
    }
}

export const gpuPointPicker = new GpuPointPicker();
