import React from "react";
import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";
import CameraControls from "camera-controls";
import {ungzip} from "pako";

import {viewerApi, type EquipmentTypeDoc} from "@/services/viewerApi";
import {useEquipmentCatalogStore} from "@/state/equipmentCatalogStore";
import {portColorInt} from "@/utils/portColor";
import {
    aabbCenterSize,
    aabbCorners,
    bboxEquals,
    bboxFromViewAabb,
    equipmentDisplayBox,
    type AabbLike,
} from "@/utils/cellbuilder/equipmentPreviewBox";

// A self-contained sidecar viewer for an equipment type: the bounding box as a
// faint wireframe, each port as a coloured nozzle arrow, and — when a CAD asset
// has been attached + inferred — the preview GLB rendered inside the box. Its
// own tiny WebGL context; it never touches the main scene, but it drives the
// camera with the same ``camera-controls`` library the main viewer uses so the
// orbit/zoom/pan feel is identical.
//
// This is also where a type's PORTS are ALIGNED: clicking a port arrow selects
// it and drops a translate/rotate gizmo on the nozzle so the user can drag it
// onto the CAD. Edits persist to the TYPE doc (`draft.doc.ports`) via the
// catalog store's `updatePort`, so Save applies them to every placed instance —
// per-instance port overrides in the cellbuilder are no longer the primary
// alignment surface.

CameraControls.install({THREE: THREE});

// three is Y-up; our model + the (now Z-up) preview GLB are Z-up. This swap
// maps between the two frames for the box/ports/proxy. It is its own inverse
// (swapping y/z twice is a no-op), so the SAME call converts model→view and
// view→model.
const swapYZ = (x: number, y: number, z: number) => new THREE.Vector3(x, z, y);

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

type PortMode = "translate" | "rotate";

const EquipmentPreview: React.FC<{
    doc: EquipmentTypeDoc;
    scope: string;
    previewKey: string | null;
}> = ({doc, scope, previewKey}) => {
    const mountRef = React.useRef<HTMLDivElement | null>(null);
    // Latest doc, read by the imperative effects/handlers without re-subscribing.
    const docRef = React.useRef(doc);
    docRef.current = doc;

    // Which port (index into doc.ports) is being edited, plus the gizmo mode and
    // whether to snap to the CAD/box. Kept in React state (drives the overlay)
    // and mirrored to a ref for the imperative gizmo code.
    const [selPort, setSelPort] = React.useState<number | null>(null);
    const [mode, setMode] = React.useState<PortMode>("translate");
    const [snap, setSnap] = React.useState(true);
    // Grow the little 3D preview to a near-fullscreen overlay — precise port
    // dragging wants a bigger canvas. The mount div stays the SAME DOM node
    // (only its container's classes change), so the WebGL context is retained;
    // the ResizeObserver resizes the renderer and a reframe re-fits the camera.
    const [expanded, setExpanded] = React.useState(false);
    const selPortRef = React.useRef(selPort);
    selPortRef.current = selPort;
    const modeRef = React.useRef(mode);
    modeRef.current = mode;
    const snapRef = React.useRef(snap);
    snapRef.current = snap;

    // Imperative three refs kept across renders.
    const stateRef = React.useRef<{
        renderer: THREE.WebGLRenderer;
        scene: THREE.Scene;
        camera: THREE.PerspectiveCamera;
        controls: CameraControls;
        content: THREE.Group; // box + ports (rebuilt on doc change)
        cad: THREE.Group | null;
        cadVerts: THREE.Vector3[]; // view-space CAD vertices, for port snapping
        proxy: THREE.Object3D; // the object the port gizmo drives (view space)
        gizmo: TransformControls;
        gizmoHelper: THREE.Object3D;
        startDir: THREE.Vector3 | null; // outward dir (view) at rotate-drag start
        hasFit: boolean;
        raf: number;
    } | null>(null);

    // Frame the camera on the current content.
    const fitToContent = (st: NonNullable<typeof stateRef.current>) => {
        const box = new THREE.Box3().setFromObject(st.content);
        if (box.isEmpty()) return;
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        if (!(sphere.radius > 0)) sphere.radius = 1;
        st.controls.minDistance = sphere.radius * 0.2;
        st.controls.maxDistance = sphere.radius * 40;
        void st.controls.fitToSphere(sphere, false);
    };

    // Seat the loaded CAD group so its min corner sits at the stored box's min
    // corner (compiler convention: min corner → cell corner). This only anchors
    // the CAD near the port frame; the wireframe box itself is then FIT to the
    // CAD's measured AABB (see displayBox), so it wraps the real geometry no
    // matter how stale/cubic the stored lx/ly/lz are. The CAD keeps its NATIVE
    // GLB orientation — no display rotation — so the preview is independent of
    // whether the (possibly old) preview GLB was authored Y-up or Z-up.
    const alignCad = (group: THREE.Group) => {
        const b = new THREE.Box3().setFromObject(group);
        if (b.isEmpty()) return;
        const {lx, ly} = docRef.current.bbox;
        const target = new THREE.Vector3(-lx / 2, 0, -ly / 2);
        group.position.add(target.sub(b.min));
    };

    // The CAD group's measured AABB in preview/view space, or null when no CAD is
    // loaded. This is the source of truth for the wireframe box + snap corners.
    const measuredCadAabb = (st: NonNullable<typeof stateRef.current>): AabbLike | null => {
        if (!st.cad) return null;
        st.cad.updateWorldMatrix(true, true);
        const b = new THREE.Box3().setFromObject(st.cad);
        if (b.isEmpty()) return null;
        return {min: [b.min.x, b.min.y, b.min.z], max: [b.max.x, b.max.y, b.max.z]};
    };

    // The box to draw/snap to: the CAD's real (non-cubic) AABB when a CAD is
    // loaded, else the stored lx/ly/lz nominal box.
    const displayBox = (st: NonNullable<typeof stateRef.current>): AabbLike =>
        equipmentDisplayBox(measuredCadAabb(st), docRef.current.bbox);

    // (Re)draw the wireframe box from displayBox(). Tagged __box so it can be
    // cleared without touching the CAD or the port arrows.
    const drawBox = (st: NonNullable<typeof stateRef.current>) => {
        for (const child of [...st.content.children]) {
            if (!child.userData.__box) continue;
            st.content.remove(child);
            const ls = child as THREE.LineSegments;
            ls.geometry?.dispose();
            (ls.material as THREE.Material | undefined)?.dispose();
        }
        const {center, size} = aabbCenterSize(displayBox(st));
        const wire = new THREE.LineSegments(
            new THREE.EdgesGeometry(new THREE.BoxGeometry(size[0], size[1], size[2])),
            new THREE.LineBasicMaterial({color: 0x94a3b8}),
        );
        wire.position.set(center[0], center[1], center[2]);
        wire.userData.__box = true;
        st.content.add(wire);
    };

    // The port's stored (model-space) anchor + outward direction, converted to
    // this preview's view space. Returns null for an out-of-range index.
    const portGeomView = (index: number): {anchor: THREE.Vector3; dir: THREE.Vector3} | null => {
        const p = docRef.current.ports[index];
        if (!p) return null;
        const anchor = swapYZ(p.position[0], p.position[1], p.position[2]);
        const dir = swapYZ(p.direction_vector[0], p.direction_vector[1], p.direction_vector[2]);
        if (dir.lengthSq() < 1e-9) dir.set(0, 1, 0);
        return {anchor, dir: dir.normalize()};
    };

    // View-space snap targets for a port move: the display-box corners (the CAD's
    // real AABB corners when a CAD is loaded, else the nominal box) + CAD vertices.
    const snapTargets = (st: NonNullable<typeof stateRef.current>): THREE.Vector3[] => {
        const corners = aabbCorners(displayBox(st)).map((c) => new THREE.Vector3(c[0], c[1], c[2]));
        return st.cadVerts.length ? corners.concat(st.cadVerts) : corners;
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

        // Port-edit gizmo: a proxy the TransformControls drives (view space, in
        // `content`'s untransformed frame == scene frame). Kept on `scene` so the
        // per-doc content rebuild doesn't clear it.
        const proxy = new THREE.Object3D();
        scene.add(proxy);
        const gizmo = new TransformControls(camera, renderer.domElement);
        gizmo.setSpace("world");
        const gizmoHelper = gizmo.getHelper();
        gizmoHelper.visible = false;
        scene.add(gizmoHelper);

        const st = {
            renderer,
            scene,
            camera,
            controls,
            content,
            cad: null as THREE.Group | null,
            cadVerts: [] as THREE.Vector3[],
            proxy,
            gizmo,
            gizmoHelper,
            startDir: null as THREE.Vector3 | null,
            hasFit: false,
            raf: 0,
        };
        stateRef.current = st;

        // While dragging the gizmo, freeze the orbit camera.
        gizmo.addEventListener("dragging-changed", (e) => {
            const dragging = Boolean((e as {value: boolean}).value);
            controls.enabled = !dragging;
            const idx = selPortRef.current;
            if (dragging && idx !== null) {
                const g = portGeomView(idx);
                st.startDir = g ? g.dir.clone() : null;
                proxy.rotation.set(0, 0, 0); // rotate delta accumulates from identity
            } else {
                st.startDir = null;
            }
        });

        gizmo.addEventListener("objectChange", () => {
            const idx = selPortRef.current;
            if (idx === null) return;
            const update = useEquipmentCatalogStore.getState().updatePort;
            if (modeRef.current === "translate") {
                // Proxy position is the dragged nozzle (view space); optional
                // snap pulls it onto the nearest box corner / CAD vertex.
                const pos = proxy.position.clone();
                if (snapRef.current) {
                    const targets = snapTargets(st);
                    const thresh = 0.15 * Math.max(1e-3, docExtent());
                    let best: THREE.Vector3 | null = null;
                    let bestD = thresh * thresh;
                    for (const t of targets) {
                        const d = t.distanceToSquared(pos);
                        if (d < bestD) {
                            bestD = d;
                            best = t;
                        }
                    }
                    if (best) {
                        pos.copy(best);
                        proxy.position.copy(best);
                    }
                }
                const model = swapYZ(pos.x, pos.y, pos.z); // view→model
                update(idx, {position: [model.x, model.y, model.z]});
            } else {
                // Rotate about the anchor: apply the proxy's accumulated rotation
                // to the outward direction captured at drag start, convert back to
                // model space, store. Position unchanged.
                if (!st.startDir) return;
                const world = st.startDir.clone().applyQuaternion(proxy.quaternion).normalize();
                const model = swapYZ(world.x, world.y, world.z).normalize(); // view→model
                update(idx, {direction_vector: [model.x, model.y, model.z]});
            }
        });

        // Click a port arrow to select it; click empty space to deselect. A drag
        // (orbit) moves the pointer, so gate selection on a near-stationary click.
        let downX = 0;
        let downY = 0;
        const onDown = (e: PointerEvent) => {
            downX = e.clientX;
            downY = e.clientY;
        };
        const onUp = (e: PointerEvent) => {
            if (Math.hypot(e.clientX - downX, e.clientY - downY) > 4) return;
            if (gizmo.dragging) return;
            const rect = renderer.domElement.getBoundingClientRect();
            const ndc = new THREE.Vector2(
                ((e.clientX - rect.left) / rect.width) * 2 - 1,
                -((e.clientY - rect.top) / rect.height) * 2 + 1,
            );
            const ray = new THREE.Raycaster();
            ray.params.Line = {threshold: 0.05 * Math.max(1e-3, docExtent())};
            ray.setFromCamera(ndc, camera);
            const hits = ray.intersectObjects(content.children, true);
            let picked: number | null = null;
            for (const h of hits) {
                let o: THREE.Object3D | null = h.object;
                while (o) {
                    if (typeof o.userData.__portIndex === "number") {
                        picked = o.userData.__portIndex;
                        break;
                    }
                    o = o.parent;
                }
                if (picked !== null) break;
            }
            setSelPort(picked);
        };
        renderer.domElement.addEventListener("pointerdown", onDown);
        renderer.domElement.addEventListener("pointerup", onUp);

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
            renderer.domElement.removeEventListener("pointerdown", onDown);
            renderer.domElement.removeEventListener("pointerup", onUp);
            gizmo.detach();
            gizmo.dispose();
            controls.dispose();
            renderer.dispose();
            if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
            stateRef.current = null;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Longest extent of the DISPLAYED box (CAD AABB when loaded, else nominal) —
    // the scale for snap/pick thresholds, so they track the real geometry size.
    const docExtent = () => {
        const st = stateRef.current;
        if (st) {
            const {size} = aabbCenterSize(displayBox(st));
            const m = Math.max(size[0], size[1], size[2]);
            if (m > 0) return m;
        }
        const {lx, ly, lz} = docRef.current.bbox;
        return Math.max(lx, ly, lz);
    };

    // ── rebuild box + ports whenever the doc changes ──────────────────
    React.useEffect(() => {
        const st = stateRef.current;
        if (!st) return;
        // clear previous port helpers (the box is redrawn by drawBox; keep CAD)
        for (const child of [...st.content.children]) {
            if (child.userData.__cad || child.userData.__box) continue;
            st.content.remove(child);
        }

        doc.ports.forEach((p, pi) => {
            const origin = swapYZ(p.position[0], p.position[1], p.position[2]);
            const d = swapYZ(p.direction_vector[0], p.direction_vector[1], p.direction_vector[2]);
            if (d.lengthSq() < 1e-9) d.set(0, 1, 0);
            d.normalize();
            // direction_vector is the outward nozzle normal; show actual flow —
            // an INPUT arrow points *into* the equipment (negate), an OUTPUT
            // arrow points out (as-is), INOUT stays outward.
            if (p.direction === "IN") d.negate();
            const len = Math.max(0.15, 0.25 * docExtent());
            const arrow = new THREE.ArrowHelper(d, origin, len, portColorInt(p, pi), len * 0.4, len * 0.25);
            // Tag the arrow (and its children) so a click resolves to this port.
            arrow.userData.__portIndex = pi;
            arrow.traverse((o) => (o.userData.__portIndex = pi));
            st.content.add(arrow);
        });

        // Keep the CAD seated on the current bbox anchor, then (re)draw the box
        // from the CAD's real AABB (or the nominal box when no CAD is loaded).
        if (st.cad) alignCad(st.cad);
        drawBox(st);

        // Frame the box the first time it appears; leave the camera alone on
        // subsequent edits.
        if (!st.hasFit) {
            fitToContent(st);
            st.hasFit = true;
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [doc]);

    // ── load / swap the CAD preview GLB ───────────────────────────────
    React.useEffect(() => {
        const st = stateRef.current;
        if (!st) return;
        let cancelled = false;
        // drop any previous CAD (then redraw the box back to the nominal dims)
        if (st.cad) {
            st.content.remove(st.cad);
            st.cad = null;
            st.cadVerts = [];
            drawBox(st);
        }
        if (!previewKey) return;
        void fetchPreviewGltf(scope, previewKey).then((group) => {
            if (cancelled || !group || !stateRef.current) return;
            group.userData.__cad = true;
            // Display the CAD in its NATIVE GLB orientation — no rotation. Existing
            // types keep whatever preview GLB they were inferred with (Y-up or
            // Z-up), so we must not assume an axis; the wireframe box is fit to the
            // CAD's measured AABB instead (drawBox), which wraps it either way.
            const cur = stateRef.current;
            cur.content.add(group);
            cur.cad = group;
            alignCad(group);
            // Cache CAD world-space (view) vertices for port snapping, capped so a
            // dense mesh doesn't blow up the nearest search.
            const verts: THREE.Vector3[] = [];
            const CAP = 4000;
            let total = 0;
            group.traverse((o) => {
                const m = o as THREE.Mesh;
                if (!m.isMesh || !m.geometry) return;
                const pos = m.geometry.getAttribute("position");
                if (!pos) return;
                total += pos.count;
            });
            const stride = Math.max(1, Math.ceil(total / CAP));
            group.updateWorldMatrix(true, true);
            group.traverse((o) => {
                const m = o as THREE.Mesh;
                if (!m.isMesh || !m.geometry) return;
                const pos = m.geometry.getAttribute("position");
                if (!pos) return;
                for (let i = 0; i < pos.count; i += stride) {
                    verts.push(new THREE.Vector3().fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld));
                }
            });
            cur.cadVerts = verts;
            drawBox(cur); // fit the wireframe box to the CAD's real AABB
            fitToContent(cur); // the CAD changes the extent — reframe on it
            // Resync the stored dims to the CAD: write the measured AABB back into
            // doc.bbox (same view→lx/ly/lz mapping the box is drawn from) so the
            // numeric fields match the wireframe and Save persists the real
            // footprint. Runs ONCE per CAD load (this effect's deps are
            // scope/previewKey); the resulting doc change only re-runs the [doc]
            // effect, which draws the box from the CAD AABB (not doc.bbox) and
            // never measures/writes — so there is no feedback loop. The equality
            // guard skips a no-op write (and its dirty flag) when already in sync.
            const aabb = measuredCadAabb(cur);
            if (aabb) {
                const next = bboxFromViewAabb(aabb);
                if (!bboxEquals(next, docRef.current.bbox)) {
                    useEquipmentCatalogStore.getState().setEquipmentDoc({bbox: next});
                }
            }
        });
        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, previewKey]);

    // ── attach / drive the port gizmo on selection + mode change ──────
    React.useEffect(() => {
        const st = stateRef.current;
        if (!st) return;
        const idx = selPort !== null && selPort < doc.ports.length ? selPort : null;
        if (idx === null) {
            st.gizmo.detach();
            st.gizmoHelper.visible = false;
            if (selPort !== null) setSelPort(null); // clamp a stale index
            return;
        }
        const g = portGeomView(idx);
        if (!g) return;
        if (!st.gizmo.dragging) {
            st.proxy.rotation.set(0, 0, 0);
            st.proxy.position.copy(g.anchor);
        }
        st.gizmo.attach(st.proxy);
        st.gizmo.setMode(mode);
        if (mode === "rotate") st.gizmo.setRotationSnap(THREE.MathUtils.degToRad(15));
        st.gizmoHelper.visible = true;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selPort, mode, doc]);

    // Re-fit the camera after the canvas grows/shrinks so the whole model stays
    // framed at the new size (the ResizeObserver has already resized the buffer).
    React.useEffect(() => {
        const st = stateRef.current;
        if (!st) return;
        const raf = requestAnimationFrame(() => {
            if (stateRef.current) fitToContent(stateRef.current);
        });
        return () => cancelAnimationFrame(raf);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [expanded]);

    const selectedName = selPort !== null ? doc.ports[selPort]?.name : undefined;

    return (
        <div className={expanded ? "fixed inset-0 z-50 bg-gray-900/90 p-4" : "relative w-full h-[200px]"}>
            <div className="relative w-full h-full">
                <div ref={mountRef} className="w-full h-full rounded-sm bg-gray-800/60 overflow-hidden" />
                <button
                    className="absolute right-2 top-2 px-1.5 py-0.5 rounded-sm text-[13px] leading-none bg-gray-900/80 text-gray-200 hover:bg-gray-700"
                    onClick={() => setExpanded((v) => !v)}
                    title={expanded ? "Collapse preview" : "Expand preview"}
                    aria-pressed={expanded}
                >
                    {expanded ? "⤡ Collapse" : "⤢"}
                </button>
                {selectedName === undefined ? (
                    doc.ports.length > 0 && (
                        <div className="absolute left-2 bottom-2 text-[11px] text-gray-300 bg-gray-900/70 rounded px-1.5 py-0.5 pointer-events-none">
                            Click a port arrow to align it
                        </div>
                    )
                ) : (
                <div className="absolute left-2 bottom-2 flex items-center gap-1 text-[11px] bg-gray-900/80 rounded px-1.5 py-1">
                    <span className="text-blue-300 font-medium max-w-[90px] truncate" title={selectedName}>
                        {selectedName}
                    </span>
                    {(
                        [
                            ["translate", "Move"],
                            ["rotate", "Rotate"],
                        ] as const
                    ).map(([m, label]) => (
                        <button
                            key={m}
                            className={
                                "px-1.5 py-0.5 rounded-sm " +
                                (mode === m ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300 hover:bg-gray-600")
                            }
                            onClick={() => setMode(m)}
                            aria-pressed={mode === m}
                        >
                            {label}
                        </button>
                    ))}
                    <label
                        className="flex items-center gap-0.5 text-gray-300"
                        title="Snap the nozzle to the box corners / CAD vertices"
                    >
                        <input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} />
                        snap
                    </label>
                        <button
                            className="px-1.5 py-0.5 rounded-sm bg-gray-700 text-gray-300 hover:bg-gray-600"
                            onClick={() => setSelPort(null)}
                            title="Finish editing this port"
                        >
                            Done
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default EquipmentPreview;
