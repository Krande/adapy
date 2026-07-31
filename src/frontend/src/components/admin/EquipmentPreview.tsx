import React from "react";
import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import CameraControls from "camera-controls";
import {ungzip} from "pako";

import {viewerApi, type EquipmentTypeDoc} from "@/services/viewerApi";
import {portColorInt} from "@/utils/portColor";

// A self-contained sidecar viewer for an equipment type: the bounding box as a
// faint wireframe, each port as a coloured nozzle arrow (updates live as the
// user edits ports), and — when a CAD asset has been attached + inferred — the
// preview GLB rendered inside the box. Its own tiny WebGL context; it never
// touches the main scene, but it drives the camera with the same
// ``camera-controls`` library the main viewer uses so the orbit/zoom/pan feel
// is identical (and not the hand-rolled camera that used to break on resize).

CameraControls.install({THREE: THREE});

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
        controls: CameraControls;
        content: THREE.Group; // box + ports (rebuilt on doc change)
        cad: THREE.Group | null;
        hasFit: boolean;
        raf: number;
    } | null>(null);

    // Frame the camera on the current content. Called once the box/ports are up
    // and again when a CAD asset loads (which changes the extent); port tweaks
    // deliberately do NOT refit so the camera stays put while you edit.
    const fitToContent = (st: NonNullable<typeof stateRef.current>) => {
        const box = new THREE.Box3().setFromObject(st.content);
        if (box.isEmpty()) return;
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        if (!(sphere.radius > 0)) sphere.radius = 1;
        st.controls.minDistance = sphere.radius * 0.2;
        st.controls.maxDistance = sphere.radius * 40;
        void st.controls.fitToSphere(sphere, false);
    };

    // ── one-time scene setup ──────────────────────────────────────────
    React.useEffect(() => {
        const mount = mountRef.current;
        if (!mount) return;
        const width = mount.clientWidth || 320;
        const height = mount.clientHeight || 200;
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

        // Same controller the main viewer uses — proper orbit/dolly/truck with
        // momentum, bound to this preview's own canvas.
        const controls = new CameraControls(camera, renderer.domElement);
        controls.setLookAt(3, 3, 3, 0, 0, 0, false);

        const st = {
            renderer,
            scene,
            camera,
            controls,
            content,
            cad: null as THREE.Group | null,
            hasFit: false,
            raf: 0,
        };
        stateRef.current = st;

        // Track the container size so the aspect ratio and canvas stay correct
        // wherever the panel is mounted (floating card, or the admin tab whose
        // width differs) — the old preview measured width once and distorted.
        const ro = new ResizeObserver(() => {
            const w = mount.clientWidth || width;
            const h = mount.clientHeight || height;
            renderer.setSize(w, h);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        });
        ro.observe(mount);

        const clock = new THREE.Clock();
        const animate = () => {
            st.raf = requestAnimationFrame(animate);
            controls.update(clock.getDelta());
            renderer.render(scene, camera);
        };
        animate();

        return () => {
            cancelAnimationFrame(st.raf);
            ro.disconnect();
            controls.dispose();
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
            const arrow = new THREE.ArrowHelper(d, origin, len, portColorInt(p), len * 0.4, len * 0.25);
            st.content.add(arrow);
        }

        // Frame the box the first time it appears; leave the camera alone on
        // subsequent port edits.
        if (!st.hasFit) {
            fitToContent(st);
            st.hasFit = true;
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
            const cur = stateRef.current;
            cur.content.add(group);
            cur.cad = group;
            fitToContent(cur); // the CAD changes the extent — reframe on it
        });
        return () => {
            cancelled = true;
        };
    }, [scope, previewKey]);

    return <div ref={mountRef} className="w-full h-[200px] rounded-sm bg-gray-800/60 overflow-hidden" />;
};

export default EquipmentPreview;
