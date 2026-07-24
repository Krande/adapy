import React from "react";
import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {ungzip} from "pako";

import {viewerApi, type CatalogPort, type EquipmentTypeDoc} from "@/services/viewerApi";

// A self-contained sidecar viewer for an equipment type: the bounding box as a
// faint wireframe, each port as a coloured nozzle arrow (updates live as the
// user edits ports), and — when a CAD asset has been attached + inferred — the
// preview GLB rendered inside the box. Its own tiny WebGL context; it never
// touches the main scene.

const CATEGORY_COLOR: Record<CatalogPort["category"], number> = {
    process: 0x38bdf8, // cyan
    electrical: 0xf59e0b, // amber
    signal: 0xec4899, // pink
};

async function fetchPreviewGltf(scope: string, key: string): Promise<THREE.Group | null> {
    const buf = await viewerApi.getBlob(scope, key);
    let bytes: Uint8Array<ArrayBufferLike> = new Uint8Array(buf);
    // Preview GLBs are gzip-at-rest; a raw blob GET may hand us the compressed
    // bytes. Sniff the gzip magic and inflate before parsing.
    if (bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
        bytes = ungzip(bytes);
    }
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    return await new Promise((resolve) => {
        new GLTFLoader().parse(
            ab,
            "",
            (gltf) => resolve(gltf.scene),
            () => resolve(null),
        );
    });
}

const EquipmentPreview: React.FC<{
    doc: EquipmentTypeDoc;
    scope: string;
    previewKey: string | null;
}> = ({doc, scope, previewKey}) => {
    const mountRef = React.useRef<HTMLDivElement | null>(null);
    // Imperative three refs kept across renders.
    const stateRef = React.useRef<{
        renderer: THREE.WebGLRenderer;
        scene: THREE.Scene;
        camera: THREE.PerspectiveCamera;
        content: THREE.Group; // box + ports (rebuilt on doc change)
        cad: THREE.Group | null;
        azim: number;
        polar: number;
        raf: number;
    } | null>(null);

    // ── one-time scene setup ──────────────────────────────────────────
    React.useEffect(() => {
        const mount = mountRef.current;
        if (!mount) return;
        const width = mount.clientWidth || 320;
        const height = 200;
        const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(width, height);
        mount.appendChild(renderer.domElement);

        const scene = new THREE.Scene();
        scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 1.1));
        const dir = new THREE.DirectionalLight(0xffffff, 0.8);
        dir.position.set(3, 5, 4);
        scene.add(dir);

        const camera = new THREE.PerspectiveCamera(45, width / height, 0.01, 1000);
        const content = new THREE.Group();
        scene.add(content);

        const st = {renderer, scene, camera, content, cad: null as THREE.Group | null, azim: 0.9, polar: 1.1, raf: 0};
        stateRef.current = st;

        // pointer-drag orbit
        let dragging = false;
        let lastX = 0;
        let lastY = 0;
        const onDown = (e: PointerEvent) => {
            dragging = true;
            lastX = e.clientX;
            lastY = e.clientY;
        };
        const onMove = (e: PointerEvent) => {
            if (!dragging) return;
            st.azim -= (e.clientX - lastX) * 0.01;
            st.polar = Math.max(0.2, Math.min(Math.PI - 0.2, st.polar - (e.clientY - lastY) * 0.01));
            lastX = e.clientX;
            lastY = e.clientY;
        };
        const onUp = () => {
            dragging = false;
        };
        renderer.domElement.addEventListener("pointerdown", onDown);
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);

        const animate = () => {
            st.raf = requestAnimationFrame(animate);
            if (!dragging) st.azim += 0.003; // gentle auto-rotate
            const box = new THREE.Box3().setFromObject(content);
            const sphere = box.getBoundingSphere(new THREE.Sphere());
            const r = sphere.radius || 1;
            const d = r * 3.2;
            camera.position.set(
                sphere.center.x + d * Math.sin(st.polar) * Math.cos(st.azim),
                sphere.center.y + d * Math.cos(st.polar),
                sphere.center.z + d * Math.sin(st.polar) * Math.sin(st.azim),
            );
            camera.lookAt(sphere.center);
            camera.near = Math.max(0.01, d - r * 4);
            camera.far = d + r * 4;
            camera.updateProjectionMatrix();
            renderer.render(scene, camera);
        };
        animate();

        return () => {
            cancelAnimationFrame(st.raf);
            renderer.domElement.removeEventListener("pointerdown", onDown);
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            renderer.dispose();
            if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
            stateRef.current = null;
        };
    }, []);

    // ── rebuild box + ports whenever the doc changes ──────────────────
    React.useEffect(() => {
        const st = stateRef.current;
        if (!st) return;
        // clear previous box/port helpers (keep any loaded CAD)
        for (const child of [...st.content.children]) {
            if (child.userData.__cad) continue;
            st.content.remove(child);
        }
        // three uses Y-up; our model is Z-up. Map (x,y,z)->(x,z,y) for display.
        const toView = (x: number, y: number, z: number) => new THREE.Vector3(x, z, y);
        const {lx, ly, lz} = doc.bbox;
        const boxGeom = new THREE.BoxGeometry(lx, lz, ly);
        const wire = new THREE.LineSegments(
            new THREE.EdgesGeometry(boxGeom),
            new THREE.LineBasicMaterial({color: 0x94a3b8}),
        );
        wire.position.copy(toView(0, 0, lz / 2)); // box base at z=0, centered in x/y
        st.content.add(wire);

        for (const p of doc.ports) {
            const origin = toView(p.position[0], p.position[1], p.position[2]);
            const d = toView(p.direction_vector[0], p.direction_vector[1], p.direction_vector[2]);
            if (d.lengthSq() < 1e-9) d.set(0, 1, 0);
            d.normalize();
            const len = Math.max(0.15, 0.25 * Math.max(lx, ly, lz));
            const arrow = new THREE.ArrowHelper(d, origin, len, CATEGORY_COLOR[p.category], len * 0.4, len * 0.25);
            st.content.add(arrow);
        }
    }, [doc]);

    // ── load / swap the CAD preview GLB ───────────────────────────────
    React.useEffect(() => {
        const st = stateRef.current;
        if (!st) return;
        let cancelled = false;
        // drop any previous CAD
        if (st.cad) {
            st.content.remove(st.cad);
            st.cad = null;
        }
        if (!previewKey) return;
        void fetchPreviewGltf(scope, previewKey).then((group) => {
            if (cancelled || !group || !stateRef.current) return;
            group.userData.__cad = true;
            // GLBs are Y-up already; drop it in as-is (centered on the box origin).
            stateRef.current.content.add(group);
            stateRef.current.cad = group;
        });
        return () => {
            cancelled = true;
        };
    }, [scope, previewKey]);

    return <div ref={mountRef} className="w-full h-[200px] rounded-sm bg-gray-800/60 overflow-hidden" />;
};

export default EquipmentPreview;
