import React from "react";
import * as THREE from "three";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";

import {sceneRef, rendererRef, cameraRef, controlsRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useSectionStore} from "@/state/sectionStore";
import {useModelState} from "@/state/modelState";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {createPlaneStencilGroup, createCapMesh, orientCapToPlane} from "@/utils/scene/section_caps";

// Headless: reconciles the section-plane store with three.js (per-material
// clipping + stencil caps + a drag gizmo). Renders nothing.
const SectionPlanesController: React.FC = () => {
    React.useEffect(() => {
        let cleanup: (() => void) | null = null;
        let raf = 0;

        const tryInit = () => {
            const renderer = rendererRef.current;
            const scene = sceneRef.current;
            const camera = cameraRef.current;
            if (!renderer || !scene || !camera) {
                raf = requestAnimationFrame(tryInit);  // wait for ThreeCanvas to set refs
                return;
            }
            cleanup = init(renderer, scene, camera);
        };
        tryInit();

        return () => {
            cancelAnimationFrame(raf);
            cleanup?.();
        };
    }, []);

    return null;
};

function init(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
): () => void {
    {
        renderer.localClippingEnabled = true;

        const container = new THREE.Group();
        container.name = "__section_planes__";
        scene.add(container);

        // Live THREE.Plane per store-plane id; the SAME instances are shared by
        // the geometry materials, stencil groups, caps and helpers, so mutating
        // a plane's constant during a gizmo drag updates everything at once.
        const planeById = new Map<string, THREE.Plane>();
        const capById = new Map<string, THREE.Mesh>();

        // Gizmo: drags a handle along the active plane's normal.
        const handle = new THREE.Object3D();
        scene.add(handle);
        const gizmo = new TransformControls(camera, renderer.domElement);
        gizmo.setMode("translate");
        gizmo.setSpace("world");
        const gizmoHelper = gizmo.getHelper();
        gizmoHelper.visible = false;
        scene.add(gizmoHelper);

        gizmo.addEventListener("dragging-changed", (e: any) => {
            if (controlsRef.current) controlsRef.current.enabled = !e.value;
            if (!e.value) {
                // Commit final position to the store (triggers a clean rebuild).
                const id = useSectionStore.getState().activeId;
                const sp = useSectionStore.getState().planes.find((p) => p.id === id);
                if (id && sp) {
                    const n = new THREE.Vector3(...sp.normal);
                    useSectionStore.getState().setConstant(id, -n.dot(handle.position));
                }
            }
            requestRender();
        });
        gizmo.addEventListener("objectChange", () => {
            const id = useSectionStore.getState().activeId;
            if (!id) return;
            const plane = planeById.get(id);
            const sp = useSectionStore.getState().planes.find((p) => p.id === id);
            const cap = capById.get(id);
            if (!plane || !sp) return;
            // Live update without a store write (no rebuild mid-drag).
            const n = new THREE.Vector3(...sp.normal);
            plane.constant = -n.dot(handle.position);
            if (cap) orientCapToPlane(cap, plane);
            requestRender();
        });

        const batchedGeometries = (): THREE.BufferGeometry[] => {
            const geoms: THREE.BufferGeometry[] = [];
            scene.traverse((o) => {
                if (o instanceof CustomBatchedMesh) geoms.push(o.geometry);
            });
            return geoms;
        };

        const applyMaterialClipping = (planes: THREE.Plane[]) => {
            const cp = planes.length ? planes : null;
            scene.traverse((o) => {
                if (o instanceof CustomBatchedMesh) {
                    const mats = Array.isArray(o.material) ? o.material : [o.material];
                    for (const m of mats) {
                        (m as THREE.Material).clippingPlanes = cp;
                        (m as THREE.Material).clipShadows = true;
                        (m as THREE.Material).needsUpdate = true;
                    }
                } else if ((o as THREE.LineSegments).isLineSegments) {
                    // Edge overlay (custom ShaderMaterial w/ clipping:true) — cut it too.
                    const mat: any = (o as THREE.LineSegments).material;
                    if (mat?.uniforms?.uVisibleTex) {
                        mat.clippingPlanes = cp;
                        mat.needsUpdate = true;
                    }
                }
            });
        };

        const disposeContainer = () => {
            for (let i = container.children.length - 1; i >= 0; i--) {
                const o = container.children[i];
                o.traverse((m: any) => {
                    if (m.userData?.__sectionCap && m.geometry?.dispose) m.geometry.dispose();
                    if (m.material) {
                        const mm = m.material;
                        (Array.isArray(mm) ? mm : [mm]).forEach((x: any) => x.dispose?.());
                    }
                });
                container.remove(o);
            }
            planeById.clear();
            capById.clear();
        };

        const rebuild = () => {
            if (!sceneRef.current) return;
            disposeContainer();

            const st = useSectionStore.getState();
            const enabled = st.planes.filter((p) => p.enabled);
            const planes = enabled.map((p) => {
                const pl = new THREE.Plane(new THREE.Vector3(...p.normal).normalize(), p.constant);
                planeById.set(p.id, pl);
                return pl;
            });

            applyMaterialClipping(planes);

            const bb = useModelState.getState().boundingBox;
            const size = bb ? bb.getSize(new THREE.Vector3()).length() * 1.1 : 50;
            const capColor = new THREE.Color(st.capColor);
            const geoms = planes.length ? batchedGeometries() : [];

            enabled.forEach((sp, i) => {
                const plane = planeById.get(sp.id)!;
                const order = i * 2 + 1;

                const helper = new THREE.PlaneHelper(plane, size, 0x2266ff);
                helper.layers.set(1);
                container.add(helper);

                for (const g of geoms) container.add(createPlaneStencilGroup(g, plane, order));

                const others = planes.filter((pl) => pl !== plane);
                const cap = createCapMesh(plane, size, capColor, others, order + 1);
                cap.userData.__sectionCap = true;
                capById.set(sp.id, cap);
                container.add(cap);
            });

            // Gizmo follows the active (and enabled) plane — unless hidden, so
            // the user can navigate without grabbing it.
            const active = enabled.find((p) => p.id === st.activeId);
            const activePlane = active ? planeById.get(active.id) : undefined;
            if (active && activePlane && st.gizmoVisible) {
                handle.position.copy(activePlane.coplanarPoint(new THREE.Vector3()));
                gizmo.showX = Math.abs(active.normal[0]) > 0.5;
                gizmo.showY = Math.abs(active.normal[1]) > 0.5;
                gizmo.showZ = Math.abs(active.normal[2]) > 0.5;
                gizmo.attach(handle);
                gizmo.enabled = true;
                gizmoHelper.visible = true;
            } else {
                gizmo.detach();
                gizmo.enabled = false;        // ignore pointer when hidden/none
                gizmoHelper.visible = false;
            }
            requestRender();
        };

        rebuild();
        const unsubSection = useSectionStore.subscribe(rebuild);
        // New model -> the CustomBatchedMesh set changed; re-apply clipping/caps.
        const unsubModel = useModelState.subscribe((s, prev) => {
            if (s.boundingBox !== prev.boundingBox) rebuild();
        });

        return () => {
            unsubSection();
            unsubModel();
            gizmo.detach();
            gizmo.dispose();
            scene.remove(gizmoHelper);
            scene.remove(handle);
            disposeContainer();
            scene.remove(container);
            applyMaterialClipping([]);  // restore unclipped materials
            requestRender();
        };
    }
}

export default SectionPlanesController;
